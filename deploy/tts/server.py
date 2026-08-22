"""Persian text-to-speech service for PadyarAIChatbot.

Runs Chatterbox (Thomcles/Chatterbox-TTS-Persian-Farsi fine-tune) on the
host's Tesla P40s and answers on 127.0.0.1:8003. Both chatbot installs call it
over loopback; it is never exposed to the internet.

FIVE THINGS SHAPE THIS FILE:

1. float32 only. The P40's FP16 throughput is 1/64 of its FP32 throughput, so
   half precision is not an optimisation here, it is a slowdown.

2. One generation at a time PER CARD. This is an autoregressive model on a
   2016 card. Two concurrent generations on the same card do not halve
   latency, they double both, so each card gets one model instance and one
   single-thread executor. TTS_WORKERS says how many cards are in play; the
   box has two P40s and the second one was sitting at 0 MiB used.

3. Waiting is a coroutine, never a thread. Every request handler here is
   async. FastAPI dispatches a SYNC handler to anyio's worker pool, which is
   40 threads wide, so 40 queued generations used to park every thread the
   server had and a request whose audio was ALREADY ON DISK could not be
   served at all. Measured: 20 cache hits behind 45 in-flight generations
   waited 41 seconds. Nothing on the request path may block the loop, which
   is why file reads go through FileResponse and ffmpeg goes through
   anyio.to_thread.

4. Disk cache, checked first. The chatbot's Tier-0/Tier-1 answers are fixed
   text from the dataset table, so the same string is synthesised over and
   over. Cached audio turns the common case into a file read, and /prerender
   lets the admin panel warm every dataset answer at save time, which is what
   makes a slow card acceptable in front of a live visitor. Identical
   requests arriving together are coalesced onto one generation.

5. mp3 out, wav in. Answers ship as 64 kbps mono mp3. A mean clip was 919 KB
   of raw wav, and at 40+ concurrent cache hits pushing those bytes was
   itself the bottleneck (200 ms per request on loopback). Reference clips
   for voice cloning stay wav: those are model INPUT, not output.
"""
import asyncio
import datetime
import hashlib
import io
import itertools
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Set, Tuple

import anyio
import numpy as np
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [tts] %(message)s",
)
logger = logging.getLogger("padyar-tts")

# The MULTILINGUAL checkpoint dir, not chatterbox-fa. The Persian fine-tune has
# a 2454-token embedding and only loads into Chatterbox Multilingual; pairing it
# with the English base fails with a text_emb size mismatch (704 vs 2454).
MODEL_DIR = os.getenv("TTS_MODEL_DIR", "/var/lib/padyar/tts/models/chatterbox-mtl")
CACHE_DIR = os.getenv("TTS_CACHE_DIR", "/var/lib/padyar/tts/cache")
VOICES_DIR = os.getenv("TTS_VOICES_DIR", "/var/lib/padyar/tts/voices")
DEVICE = os.getenv("TTS_DEVICE", "cuda")
# Chatterbox degrades on very long inputs; the service splits on sentence
# boundaries and joins the waveforms instead of truncating.
MAX_CHARS_PER_CHUNK = int(os.getenv("TTS_CHUNK_CHARS", "280"))
MAX_TEXT_CHARS = int(os.getenv("TTS_MAX_CHARS", "4000"))
# Persian. NOT one of the base model's 23 supported languages — the fine-tune
# added it — so SUPPORTED_LANGUAGES is patched at load time or generate()
# rejects it outright.
LANGUAGE = os.getenv("TTS_LANGUAGE", "fa")
# Measured on this host: 8 threads -> RTF 36, 32 threads -> RTF 5.4. Past ~32
# the curve flattens, and oversubscribing the box hurts the two chatbots.
CPU_THREADS = int(os.getenv("TTS_CPU_THREADS", "32"))
# One model instance per worker, one worker per GPU. Default 1 because a
# single-card install is a real customer configuration, not a hypothetical.
# This host has two P40s and sets 2 in its systemd unit; an instance costs
# about 7.4 GB of a 24 GB card, so both fit with room to spare.
TTS_WORKERS = max(1, int(os.getenv("TTS_WORKERS", "1")))

# --- output encoding -------------------------------------------------------
# 64 kbps mono mp3. A real 115.9-second answer out of this installation's own
# cache is 5430 KB as wav and 905 KB at this bitrate, and speech at 64k is not
# distinguishable from the wav on a phone speaker in an exhibition hall. Opus
# would be smaller again, but older iOS Safari will not play it and visitors
# open this on their own phones, so mp3 is the format that always works.
MP3_BITRATE = os.getenv("TTS_MP3_BITRATE", "64k")
AUDIO_MEDIA_TYPE = "audio/mpeg"
AUDIO_SUFFIX = ".mp3"

# --- reference clips for voice cloning -------------------------------------
# The app user (padyar-inotex) cannot write into VOICES_DIR — the directory
# belongs to the TTS service's own user — so an admin uploading a sample has to
# come through this service. That is what POST/DELETE /voices exist for.
#
# 24 kHz mono 16-bit is what Chatterbox reads a prompt at; converting on the way
# IN means the generation path never pays for a resample, and means an operator
# can hand us whatever their phone recorded.
VOICE_SAMPLE_RATE = 24000
# Chatterbox's own guidance is a 5–20 second clip. The accepted band is a little
# wider on purpose: refusing a good 4-second recording would be pedantry, and
# the UI already steers people to 5–20. Past 30s the model starts ignoring the
# tail, so accepting it would only waste the operator's time.
MIN_VOICE_SECONDS = 3.0
MAX_VOICE_SECONDS = 30.0
# A ceiling on what we will even read from the socket. 25 MB is far more than a
# 30-second clip in any sane codec; anything bigger is a mistake or an attack,
# and either way must not be spooled to disk or handed to ffmpeg.
MAX_VOICE_UPLOAD_BYTES = 25 * 1024 * 1024
# Filenames become part of a path. Only these characters ever reach the
# filesystem — no dots, no separators, so ".." and "/etc/passwd" cannot be
# expressed at all rather than being detected and rejected.
_VOICE_NAME_ALLOWED = re.compile(r"[^A-Za-z0-9_-]")
MAX_VOICE_NAME_CHARS = 48

# Set BEFORE the model is built. In a standalone process that calls
# set_num_threads() first, this text runs at RTF 5; the service, which set it
# after from_local(), ran the same text at RTF 16-18 with only ~5 cores busy.
# PyTorch/OpenMP sizes its team on first use, so the order is not cosmetic.
if DEVICE == "cpu":
    torch.set_num_threads(CPU_THREADS)

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(VOICES_DIR, exist_ok=True)

app = FastAPI(title="Padyar TTS", docs_url=None, redoc_url=None)

_model_error: Optional[str] = None


class Worker:
    """One model instance, one card, one thread.

    Every generation runs on THIS thread, never on the request handler's.

    FastAPI dispatches a sync endpoint to an anyio worker thread, and PyTorch's
    OpenMP team on such a thread came up far smaller than the configured 32:
    measured at ~5 cores busy and RTF 19, against RTF 5.3 for identical text in
    a standalone process. Pinning generation to a single long-lived thread,
    whose thread count is set once from inside that thread, restores the
    standalone figure. max_workers=1 also enforces one generation at a time on
    this card, which the hardware wants anyway, so this replaces the old lock
    rather than adding to it.

    What CHANGED with the async handlers is who waits on the queue. A queued
    generation now costs a coroutine, so the 40 anyio threads stay free to
    serve cache hits. The queue is as deep as it always was; waiting in it is
    just no longer paid for with a thread.

    The MODEL is built on the pool thread too. Its CUDA context then belongs to
    the thread that will use it, and the thread config is applied before any
    torch work happens rather than after.
    """

    def __init__(self, index: int, device: str):
        self.index = index
        self.device = device
        self.pool = ThreadPoolExecutor(max_workers=1,
                                       thread_name_prefix=f"tts-gen{index}")
        self.model = None
        self._threads_set = threading.Event()

    def load(self):
        """Build the model now, on the pool thread. Raises what load_model raises."""
        return self.pool.submit(self._ensure_model).result()

    def submit(self, run: Callable) -> Future:
        """Queue run(model) on this worker's thread."""
        return self.pool.submit(lambda: run(self._ensure_model()))

    def _ensure_model(self):
        if self.model is None:
            if not self._threads_set.is_set():
                logger.info("worker %d using %d torch threads",
                            self.index, self._apply_thread_config())
            self.model = load_model(self.device)
        return self.model

    def _apply_thread_config(self) -> int:
        """Run once, ON the generation thread. The setting does not travel."""
        if DEVICE == "cpu":
            torch.set_num_threads(CPU_THREADS)
        self._threads_set.set()
        return torch.get_num_threads()


# Filled by _load_workers() at startup. Empty means nothing loaded, and /health
# explains why instead of the process crash-looping.
_workers: List[Worker] = []
_worker_turn = itertools.count()
# Serialises a retry after a failed startup load, so fifty queued requests do
# not each start their own multi-GB load.
_load_lock = asyncio.Lock()


def worker_devices() -> List[str]:
    """One device string per worker.

    A single worker keeps whatever TTS_DEVICE says, so the default install is
    byte for byte what it always was. Past that, worker i takes cuda:i. The
    systemd unit used to hide the second P40 behind CUDA_VISIBLE_DEVICES=0; if
    it is still hidden, load_model() says so out loud instead of quietly
    putting both models on card 0 and halving nothing.
    """
    if TTS_WORKERS <= 1 or not DEVICE.startswith("cuda"):
        return [DEVICE] * TTS_WORKERS
    return [f"cuda:{i}" for i in range(TTS_WORKERS)]


def pick_worker() -> Worker:
    """Round robin. Called only from the event loop thread, so no lock.

    Queue depth would be the smarter rule, but the cards are identical and the
    work per request is not knowable in advance, so it would land on the same
    answer while being harder to reason about.
    """
    if not _workers:
        raise HTTPException(
            status_code=503,
            detail=f"the speech model is not loaded: {_model_error or 'unknown error'}")
    return _workers[next(_worker_turn) % len(_workers)]


# --- an unrecoverable card -------------------------------------------------
# A device-side assert (an out-of-range embedding index, for one unlucky piece
# of text) does not fail one generation. It poisons the process's whole CUDA
# context: every later call raises the same error until the process is gone.
#
# This is not hypothetical. Production sat in exactly that state for six hours
# on 2026-08-22 — /health still said model_loaded:true, because the model
# object was fine, while every single /tts returned an opaque 500. Nothing
# noticed, because nothing was looking.
#
# There is no in-process repair for it. Exiting IS the repair, and the unit's
# Restart=always turns a permanent outage into about twenty-five seconds of one.
_CUDA_FATAL = ("device-side assert", "CUDA error",
               "an illegal memory access", "unspecified launch failure")


def is_cuda_context_poisoned(exc: BaseException) -> bool:
    """Is this the sticky kind of CUDA failure, or just a bad request?"""
    if not isinstance(exc, RuntimeError) or not DEVICE.startswith("cuda"):
        return False
    return any(marker in str(exc) for marker in _CUDA_FATAL)


def die_on_poisoned_context(exc: BaseException) -> None:
    """Say why in the journal, give this caller their answer, then exit."""
    logger.critical("CUDA context is unrecoverable, exiting so systemd "
                    "rebuilds the process: %s", exc)

    def _exit() -> None:
        # Long enough for the 503 below to reach the caller, short enough that
        # the next visitor meets a restarting service rather than a dead one.
        time.sleep(1.0)
        # os._exit, not sys.exit: this runs on a worker thread, where
        # SystemExit would be swallowed and the process would live on poisoned.
        os._exit(70)

    threading.Thread(target=_exit, name="tts-die", daemon=True).start()


# --- text handling ---------------------------------------------------------

# Persian sentence enders, plus the Latin ones that appear in mixed text.
_SENTENCE_END = re.compile(r"(?<=[.!?؟؛\n])\s+")
# Control characters only. NOT ‌ (ZWNJ) — that is a real Persian
# character and removing it changes how words are pronounced.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize(text: str) -> str:
    """Minimal cleanup. Deliberately NOT the app's Persian normalizer.

    app/utils/normalizer.py exists to make retrieval match — it folds
    characters and expands synonyms, which is exactly wrong for speech, where
    the literal written form is what should be read aloud.
    """
    text = _CONTROL.sub("", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def split_for_synthesis(text: str) -> List[str]:
    """Sentence-aware chunks under MAX_CHARS_PER_CHUNK."""
    chunks, current = [], ""
    for sentence in _SENTENCE_END.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > MAX_CHARS_PER_CHUNK:
            # A single over-long sentence: fall back to comma, then a hard cut.
            for part in re.split(r"(?<=[،,])\s*", sentence):
                while len(part) > MAX_CHARS_PER_CHUNK:
                    chunks.append(part[:MAX_CHARS_PER_CHUNK])
                    part = part[MAX_CHARS_PER_CHUNK:]
                if part:
                    chunks.append(part)
            continue
        if len(current) + len(sentence) + 1 <= MAX_CHARS_PER_CHUNK:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks or [text]


# --- model -----------------------------------------------------------------

def _guard_speech_tokens(model) -> None:
    """Drop speech tokens the vocoder has no embedding for.

    UPSTREAM BUG, and the direct cause of a six-hour outage on 2026-08-22.
    T3 SAMPLES its speech tokens, so it occasionally emits an id past the end
    of the 6561-entry vocabulary. The MONOLINGUAL model filters exactly that
    (chatterbox/tts.py: `speech_tokens = speech_tokens[speech_tokens < 6561]`,
    and tts_turbo.py has it too); mtl_tts.py, the multilingual model this
    install runs, is missing the line. s3gen's flow.py then notices the bad id,
    logs "6598.0>6561", and indexes with it ANYWAY — which fires a device-side
    assert and poisons the CUDA context for the whole process.

    Because it is sampled, the same text can generate a hundred times and fail
    on the hundred and first. That is why this looked like a bad piece of text
    for a while: it is not the text, it is the dice.

    Patched at the vocoder boundary rather than in site-packages: an edit there
    would be silently undone by a reinstall of the wheel, and this is the same
    seam the monolingual filter protects.
    """
    from chatterbox.models.s3tokenizer import SPEECH_VOCAB_SIZE

    inner = model.s3gen.inference

    def inference(*, speech_tokens, **kwargs):
        keep = speech_tokens < SPEECH_VOCAB_SIZE
        if not bool(keep.all()):
            logger.warning(
                "dropped %d speech token(s) outside the %d-entry vocabulary "
                "(highest %d) — this generation would otherwise have taken the "
                "CUDA context down",
                int((~keep).sum()), SPEECH_VOCAB_SIZE, int(speech_tokens.max()))
            speech_tokens = speech_tokens[keep]
        return inner(speech_tokens=speech_tokens, **kwargs)

    model.s3gen.inference = inference


def load_model(device: str = DEVICE):
    """Build ONE model instance on `device`. One call per worker.

    Not memoised on purpose: with two cards there are two instances, and a
    module-level singleton would hand the second worker the first card's model.
    Worker._ensure_model() holds the per-worker instance.
    """
    from chatterbox import mtl_tts
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    # generate() validates language_id against this dict and raises for "fa".
    mtl_tts.SUPPORTED_LANGUAGES.setdefault("fa", "Persian")

    if device.startswith("cuda"):
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available to torch")
        index = int(device.split(":", 1)[1]) if ":" in device else 0
        count = torch.cuda.device_count()
        if index >= count:
            raise RuntimeError(
                f"{device} was asked for but torch can see {count} CUDA "
                f"device(s); check CUDA_VISIBLE_DEVICES in the systemd unit")
        # Set the current device BEFORE the model is built. Anything inside
        # chatterbox that says .cuda() or device="cuda" with no index lands on
        # the CURRENT device, so without this a second worker could silently
        # put its weights on card 0 and leave the second card idle, which is
        # the exact problem this is here to fix.
        torch.cuda.set_device(index)
        # NOT a string match on get_arch_list(). The cu124 wheel ships sm_50,
        # sm_60, sm_70+ and NO sm_61, yet it drives a P40 (sm_61) correctly,
        # because CUDA guarantees binary compatibility within one major
        # compute capability: sm_60 cubins run on sm_61. An earlier version of
        # this check refused to start on exactly the hardware it was written
        # for. The only honest test is to launch a kernel.
        cap = torch.cuda.get_device_capability(index)
        try:
            probe = torch.zeros(64, 64, device=device)
            (probe @ probe).sum().item()
            torch.cuda.synchronize(index)
        except Exception as exc:                  # noqa: BLE001
            raise RuntimeError(
                f"CUDA kernels will not run on {torch.cuda.get_device_name(index)} "
                f"(sm_{cap[0]}{cap[1]}) with torch {torch.__version__}: {exc}"
            ) from exc
        logger.info("device %s: %s (sm_%d%d), torch %s", device,
                    torch.cuda.get_device_name(index), cap[0], cap[1], torch.__version__)

    logger.info("loading Chatterbox from %s onto %s", MODEL_DIR, device)
    started = time.perf_counter()
    model = ChatterboxMultilingualTTS.from_local(MODEL_DIR, device=device)
    _guard_speech_tokens(model)
    # float32 is deliberate, see the module docstring.
    logger.info("model ready on %s in %.0fs, sample rate %s Hz",
                device, time.perf_counter() - started, model.sr)
    return model


def _load_workers() -> None:
    """Build every worker. A card that fails does not take the others down.

    Half a service is worth far more than none: one live P40 still answers, at
    half the generation throughput, and _model_error names the card that did
    not come up so /health can say it out loud.
    """
    global _model_error
    devices = worker_devices()
    _workers.clear()
    if len(devices) > 1:
        # Startup is not fast. Say the multiplier BEFORE it happens, or an
        # operator watching systemd concludes the service has hung.
        logger.info("loading %d model instances (%s), one after another, so "
                    "startup takes about %dx as long as a single-worker start",
                    len(devices), ", ".join(devices), len(devices))
    errors = []
    for index, device in enumerate(devices):
        worker = Worker(index, device)
        started = time.perf_counter()
        try:
            worker.load()
        except Exception as exc:                  # noqa: BLE001
            errors.append(f"{device}: {type(exc).__name__}: {exc}")
            logger.error("worker %d on %s failed to load: %s", index, device, exc)
            continue
        _workers.append(worker)
        logger.info("worker %d ready on %s after %.0fs",
                    index, device, time.perf_counter() - started)
    _model_error = "; ".join(errors) or None


async def ensure_workers() -> None:
    """Load the model(s) if startup could not.

    Startup deliberately does not crash-loop, so /health can report WHY the
    service is down. That would leave a transient failure (a driver still
    settling, say) permanent, so the first request after a failure tries again.
    Off the event loop, because this reads gigabytes, and under a lock, because
    otherwise every queued request starts its own load.
    """
    if _workers:
        return
    async with _load_lock:
        if not _workers:
            await anyio.to_thread.run_sync(_load_workers)


@app.on_event("startup")
def _startup():
    try:
        _load_workers()
    except Exception as exc:                      # noqa: BLE001
        # Do not crash-loop: /health must be able to report WHY it is down.
        globals()["_model_error"] = f"{type(exc).__name__}: {exc}"
        logger.error("model failed to load: %s", _model_error)


# --- audio -----------------------------------------------------------------

def to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """Model output to a PCM wav. Still the intermediate ffmpeg encodes from."""
    audio = np.clip(samples, -1.0, 1.0)
    pcm = (audio * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(pcm.tobytes())
    return buf.getvalue()


def to_mp3_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """Model output to a 64 kbps mono mp3. BLOCKS: never call from the loop.

    ffmpeg rather than a Python encoder because this service already depends on
    it (voice uploads cannot work without it), so this adds nothing to install
    or to maintain.

    The wav goes in as a FILE, not down a pipe. -nostdin is what stops ffmpeg
    inheriting the service's stdin and blocking forever on a prompt nobody will
    ever answer, and it cannot be combined with feeding input in on stdin.
    """
    if not shutil.which("ffmpeg"):
        raise HTTPException(
            status_code=503,
            detail="ffmpeg is not installed on the TTS host; cannot encode audio")
    wav = to_wav_bytes(samples, sample_rate)
    with tempfile.TemporaryDirectory(prefix="tts-encode-") as scratch:
        src = os.path.join(scratch, "audio.wav")
        with open(src, "wb") as fh:
            fh.write(wav)
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", src,
             "-vn", "-ac", "1", "-b:a", MP3_BITRATE, "-f", "mp3", "pipe:1"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            # Encoding a few minutes of speech is well under a second. A minute
            # means something is wrong and the caller should hear about it.
            timeout=60,
        )
    if proc.returncode != 0 or not proc.stdout:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise HTTPException(
            status_code=500,
            detail="encoding the audio to mp3 failed" + (f": {tail[-1]}" if tail else ""),
        )
    return proc.stdout


def voice_fingerprint(voice: str) -> str:
    """Identity of the reference CLIP, not just the name pointing at it.

    A non-technical operator records a sample, listens, is not happy, and
    records again under the same name — that is the normal way this feature
    gets used. On the name alone the cache key would be identical both times,
    so the panel would keep playing back audio cloned from the clip that was
    just replaced, and no amount of re-recording would change it.

    mtime+size changes when the file is replaced and on nothing else, so a
    cache warmed by /prerender survives restarts and redeploys untouched.
    Unreadable (or absent) is the empty string: synthesize() reports the
    missing voice properly, and a key is never invented for a file we could
    not stat.
    """
    if not voice:
        return ""
    try:
        st = os.stat(os.path.join(VOICES_DIR, f"{voice}.wav"))
    except OSError:
        return ""
    return f"{int(st.st_mtime)}:{st.st_size}"


def cache_key(text: str, voice: str, exaggeration: float, cfg_weight: float,
              language: str = LANGUAGE, temperature: float = 0.8) -> str:
    """Every input that changes the waveform is in the key. NOTHING else is.

    Adding a field here invalidates every previously cached entry, because the
    joined string changes for the same request. That is the honest trade: a key
    that ignored temperature would hand back audio generated at a different one.
    After a change to this function, re-run deploy/45-prerender.sh to re-warm
    the dataset answers, or the first visitor of the day pays for them.
    """
    raw = "\x00".join([text, voice, f"{exaggeration:.3f}", f"{cfg_weight:.3f}",
                       language, f"{temperature:.3f}", voice_fingerprint(voice)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_path(key: str) -> str:
    """Where a key's audio lives. Pure path work, no syscalls.

    Two-level fan-out keeps the directory listing usable at scale. The suffix
    moved from .wav to .mp3 with the encoding change, so entries written before
    that are simply never looked up again: there is no migration, they age out
    on the next /cache/prune.
    """
    return os.path.join(CACHE_DIR, key[:2], f"{key}{AUDIO_SUFFIX}")


def write_cache(key: str, audio: bytes) -> str:
    """Publish one entry atomically. BLOCKS: never call from the loop.

    Write-then-replace, so a killed process or a full disk can never leave a
    half-written file that later reads as a valid cache hit.
    """
    path = cache_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(audio)
    os.replace(tmp, path)
    return path


# Persian speech runs at roughly 0.077 seconds of audio per character —
# measured across this installation's own 16 seeded answers (3968 characters
# rendering to 304 seconds). Used only as a floor, never to reject anything a
# human would accept.
SECONDS_PER_CHAR = 0.077
# A clip shorter than this fraction of the predicted length is not a short
# reading, it is a truncated one. A load test caught a 99-character sentence
# returning 0.7s of audio instead of ~12s — with HTTP 200, counted as success
# everywhere, and then CACHED, so every later visitor asking that question
# would have been served the same broken clip forever. Generation is sampled,
# so a retry usually lands correctly.
TRUNCATION_FLOOR = 0.4
# Below this there is nothing to compare against: two words legitimately
# produce a very short clip.
MIN_CHARS_TO_CHECK = 40


def looks_truncated(text: str, audio_seconds: float) -> bool:
    if len(text) < MIN_CHARS_TO_CHECK:
        return False
    expected = len(text) * SECONDS_PER_CHAR
    return audio_seconds < expected * TRUNCATION_FLOOR


async def synthesize(text: str, voice: str, exaggeration: float, cfg_weight: float,
                     language: str = LANGUAGE, temperature: float = 0.8) -> bytes:
    """Generate `text` and return it as mp3 bytes. Nothing here blocks the loop."""
    await ensure_workers()
    prompt = os.path.join(VOICES_DIR, f"{voice}.wav") if voice else ""
    # One stat on a local directory, so the loop is not meaningfully held.
    # Doing it HERE rather than inside _run means an unknown voice is a fast
    # 400 instead of a 400 that queued behind half an hour of generations.
    if prompt and not os.path.exists(prompt):
        raise HTTPException(status_code=400, detail=f"unknown voice: {voice}")

    worker = pick_worker()

    def _run(model) -> Tuple[np.ndarray, int]:
        pieces = []
        for chunk in split_for_synthesis(text):
            kwargs = {"exaggeration": exaggeration, "cfg_weight": cfg_weight,
                      "temperature": temperature}
            if prompt:
                kwargs["audio_prompt_path"] = prompt
            # language_id is positional-required on the multilingual model.
            wav = model.generate(chunk, language_id=language, **kwargs)
            pieces.append(wav.squeeze(0).detach().cpu().numpy())
            # 120 ms of silence between sentences, so joined chunks do not run
            # together into one breathless sentence.
            pieces.append(np.zeros(int(model.sr * 0.12), dtype=np.float32))
        # Raw samples, NOT encoded bytes. The truncation guard below measures
        # duration, and len(samples)/sample_rate is exact where an mp3 would
        # have to be decoded again to answer the same question.
        return np.concatenate(pieces), model.sr

    async def _generate() -> Tuple[np.ndarray, int]:
        """One attempt, with the sticky-CUDA case turned into an exit.

        await, not .result(). Waiting now costs a coroutine instead of one of
        the 40 anyio worker threads, which is the whole reason a cache hit
        arriving behind fifty misses is answered immediately instead of in 41
        seconds.
        """
        try:
            return await asyncio.wrap_future(worker.submit(_run))
        except RuntimeError as exc:
            if not is_cuda_context_poisoned(exc):
                raise
            die_on_poisoned_context(exc)
            raise HTTPException(
                status_code=503,
                detail="the GPU faulted on this text and the speech service is "
                       "restarting; try again in about half a minute") from exc

    samples, sample_rate = await _generate()

    seconds = len(samples) / float(sample_rate)
    if looks_truncated(text, seconds):
        logger.warning("truncated generation: %d chars produced %.2fs, retrying",
                       len(text), seconds)
        # Same worker on purpose: it is warm, and a retry is not a reason to
        # take the other card away from a visitor who is already waiting.
        samples, sample_rate = await _generate()
        seconds = len(samples) / float(sample_rate)
        if looks_truncated(text, seconds):
            # Refusing is the point. Returning it would cache a broken clip and
            # serve it to every visitor who asks that question afterwards.
            raise HTTPException(
                status_code=502,
                detail=(f"generation produced only {seconds:.1f}s of audio for "
                        f"{len(text)} characters, twice, so it will not be cached"))

    # ffmpeg goes to the anyio pool, NOT to the generation thread: that thread
    # should already be starting the next visitor's audio, not muxing this one's.
    return await anyio.to_thread.run_sync(to_mp3_bytes, samples, sample_rate)


# --- API -------------------------------------------------------------------

class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1)
    voice: str = Field("", description="Filename stem under the voices dir; empty = model default")
    exaggeration: float = Field(0.5, ge=0.0, le=2.0)
    cfg_weight: float = Field(0.5, ge=0.0, le=1.0)
    language: str = Field(LANGUAGE, description="language_id passed to the model")
    # Chatterbox's sampling temperature. The model's own default is 0.8 and the
    # bounds are the ones its generate() documents — kept identical here so a
    # value the panel accepts is never one the model then rejects.
    temperature: float = Field(0.8, ge=0.05, le=5.0)
    # Generation is sampled, so identical input gives an identical cache key
    # and NOT identical audio. Without this an answer that happened to come out
    # badly would be served from the cache for the rest of the exhibition, with
    # no way to ask for another take.
    force: bool = Field(False, description="regenerate and overwrite even on a cache hit")


class PrerenderRequest(BaseModel):
    texts: List[str]
    voice: str = ""
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    temperature: float = 0.8


class PruneRequest(BaseModel):
    keep: List[str] = Field(default_factory=list,
                            description="sha256 cache keys that must survive")
    # The caller almost never knows the keys — it knows the ANSWERS. Keying is
    # this service's job (cache_key folds in the voice fingerprint and every
    # generation parameter), and a caller that reimplemented it would delete
    # live entries the moment the two drifted. So texts come in, keys are
    # derived here, and there is exactly one implementation of the rule.
    keep_texts: List[str] = Field(default_factory=list,
                                  description="answers whose audio must survive")
    voice: str = ""
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    temperature: float = 0.8
    # An empty keep list means "delete everything", which is also exactly what
    # a caller whose list failed to build looks like. Wiping a cache that took
    # hours of GPU time to warm has to be asked for by name.
    delete_all: bool = Field(False, description="required to prune with an empty keep list")

    def survivors(self) -> Set[str]:
        """Every key this request wants kept, from both ways of naming one."""
        keys = {k.strip().lower() for k in self.keep if k and k.strip()}
        for raw in self.keep_texts:
            text = normalize(raw)
            if text:
                keys.add(cache_key(text, self.voice, self.exaggeration,
                                   self.cfg_weight, LANGUAGE, self.temperature))
        return keys


@app.get("/health")
def health():
    # One card up and one down is a real state on a two-card box, so this
    # reports what did load AND the error from what did not. status stays "ok"
    # while anything at all can generate.
    model = _workers[0].model if _workers else None
    return {
        "status": "ok" if _workers else "degraded",
        "model_loaded": bool(_workers),
        "error": _model_error,
        "device": DEVICE,
        "workers": [{"index": w.index, "device": w.device} for w in _workers],
        "workers_configured": TTS_WORKERS,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "sample_rate": getattr(model, "sr", None),
        "language": LANGUAGE,
        "audio_format": AUDIO_MEDIA_TYPE,
        "cpu_threads": CPU_THREADS if DEVICE == "cpu" else None,
    }


# Cache key -> the task already generating it. Ten visitors asking the same new
# question in the same moment used to queue ten identical generations on a card
# that does one at a time, so the tenth waited five minutes for audio the first
# one had produced. Now the first generates and the rest await it.
#
# Read and written only from the event loop thread, and never across an await,
# so a plain dict needs no lock.
_inflight: Dict[str, "asyncio.Task"] = {}


async def generate_once(key: str, text: str, req: "SpeakRequest") -> bytes:
    """Generate and cache `key`, or join the generation already running for it."""
    task = _inflight.get(key)
    if task is None:
        task = asyncio.create_task(
            _generate_and_store(key, text, req))
        _inflight[key] = task

        def _forget(finished: "asyncio.Task", key: str = key) -> None:
            # Always, on success, failure and cancellation alike. A key left
            # behind here would make one failure permanent for every later
            # caller, and every later caller would await a task that is done.
            _inflight.pop(key, None)
            if not finished.cancelled() and finished.exception() is not None:
                # Reading the exception here also marks it retrieved, so a
                # failure whose last awaiter already disconnected does not turn
                # into an unhandled-task warning in the journal.
                logger.warning("generation failed for %s: %s",
                               key[:12], finished.exception())

        task.add_done_callback(_forget)
    # shield: a visitor who closes the tab cancels their own request, and
    # without this that cancellation would travel into the shared task and
    # abandon everyone else waiting on the same audio.
    return await asyncio.shield(task)


async def _generate_and_store(key: str, text: str, req: "SpeakRequest") -> bytes:
    audio = await synthesize(text, req.voice, req.exaggeration, req.cfg_weight,
                             req.language, req.temperature)
    await anyio.to_thread.run_sync(write_cache, key, audio)
    return audio


@app.post("/tts")
async def tts(req: SpeakRequest):
    text = normalize(req.text)
    if not text:
        raise HTTPException(status_code=400, detail="text is empty after normalization")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail=f"text longer than {MAX_TEXT_CHARS} characters")

    key = cache_key(text, req.voice, req.exaggeration, req.cfg_weight, req.language,
                    req.temperature)
    path = cache_path(key)
    if not req.force and os.path.exists(path):
        # FileResponse, not fh.read(). The kernel already holds this file in
        # page cache; reading ~900 KB into Python only for uvicorn to copy it
        # again into the socket paid for the same bytes twice. FileResponse
        # stats and streams from a worker thread, so the loop is never held.
        #
        # If a /cache/prune deletes the file in the microseconds between the
        # check above and the send, this 500s. That is an admin action racing a
        # visitor by a hair, and the visitor's retry hits a regenerated file.
        return FileResponse(path, media_type=AUDIO_MEDIA_TYPE,
                            headers={"X-TTS-Cache": "hit", "X-TTS-Key": key})

    # force joins an in-flight generation for the same key rather than starting
    # a second one. It is still a fresh take either way, and two operators
    # clicking regenerate in the same second want one new clip, not two.
    audio = await generate_once(key, text, req)
    return Response(audio, media_type=AUDIO_MEDIA_TYPE,
                    headers={"X-TTS-Cache": "miss", "X-TTS-Key": key})


@app.post("/prerender")
async def prerender(req: PrerenderRequest):
    """Warm the cache for a batch of dataset answers.

    Called when an admin saves an entry, so the visitor-facing path is always
    a cache hit.

    The counts are what a progress display needs: how many were considered,
    made, already there, and refused. `errors` carries the text and the reason
    for each failure, because "3 failed" tells an operator nothing about which
    three. A visitor asking for one of these answers meanwhile is NOT stuck
    behind the batch: each generation is awaited, so the loop keeps serving.
    """
    total = rendered = skipped = 0
    errors = []
    for raw in req.texts:
        text = normalize(raw)
        if not text:
            continue
        total += 1
        key = cache_key(text, req.voice, req.exaggeration, req.cfg_weight,
                        temperature=req.temperature)
        if os.path.exists(cache_path(key)):
            skipped += 1
            continue
        try:
            audio = await synthesize(text, req.voice, req.exaggeration, req.cfg_weight,
                                     temperature=req.temperature)
            await anyio.to_thread.run_sync(write_cache, key, audio)
            rendered += 1
        except Exception as exc:                   # noqa: BLE001
            logger.error("prerender failed for %r: %s", text[:60], exc)
            errors.append({"text": text[:60], "error": str(exc)})
    return {"total": total, "rendered": rendered, "cached_already": skipped,
            "failed": len(errors), "errors": errors}


# --- cache management ------------------------------------------------------
#
# The admin panel drives both of these. Neither walks the cache on the event
# loop: 178 files is nothing, but a season of an exhibition is not, and a
# directory walk is exactly the kind of "it was fast on my machine" that goes
# on to hold up every visitor at once.

def _scan_cache() -> Tuple[int, int, Optional[float], Optional[float]]:
    files = size = 0
    oldest = newest = None
    for root, _dirs, names in os.walk(CACHE_DIR):
        for name in names:
            try:
                st = os.stat(os.path.join(root, name))
            except OSError:
                continue          # deleted mid-walk; there is nothing to report
            files += 1
            size += st.st_size
            oldest = st.st_mtime if oldest is None else min(oldest, st.st_mtime)
            newest = st.st_mtime if newest is None else max(newest, st.st_mtime)
    return files, size, oldest, newest


def _iso(stamp: Optional[float]) -> Optional[str]:
    if stamp is None:
        return None
    return datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).isoformat()


@app.get("/cache/stats")
async def cache_stats():
    files, size, oldest, newest = await anyio.to_thread.run_sync(_scan_cache)
    return {"files": files, "bytes": size, "oldest": _iso(oldest), "newest": _iso(newest)}


def _prune_cache(keep: Set[str]) -> Tuple[int, int]:
    deleted = freed = 0
    for root, _dirs, names in os.walk(CACHE_DIR):
        for name in names:
            stem, ext = os.path.splitext(name)
            # A file survives only if it is a current-format entry the caller
            # named. Everything else goes, which is deliberate: that sweeps up
            # the .wav entries written before the mp3 switch and any .tmp a
            # killed write left behind, without a migration to write.
            if ext == AUDIO_SUFFIX and stem in keep:
                continue
            full = os.path.join(root, name)
            try:
                size = os.stat(full).st_size
                os.remove(full)
            except OSError:
                continue
            deleted += 1
            freed += size
    return deleted, freed


@app.post("/cache/prune")
async def cache_prune(req: PruneRequest):
    keep = req.survivors()
    if not keep and not req.delete_all:
        raise HTTPException(
            status_code=400,
            detail="keep is empty; send delete_all=true to clear the whole cache")
    deleted, freed = await anyio.to_thread.run_sync(_prune_cache, keep)
    logger.info("cache prune: kept %d key(s), deleted %d file(s), freed %d bytes",
                len(keep), deleted, freed)
    return {"deleted": deleted, "freed_bytes": freed}


@app.get("/voices")
def voices():
    names = sorted(
        os.path.splitext(f)[0] for f in os.listdir(VOICES_DIR) if f.endswith(".wav")
    )
    return {"voices": names, "default": ""}


# --- voice management ------------------------------------------------------
#
# WHY THESE LIVE HERE AND NOT IN THE ADMIN APP: VOICES_DIR is owned by the TTS
# service's user under /var/lib/padyar, and the two chatbot installs run as
# padyar-inotex / padyar-elecomp. They can read a voice's NAME through the API
# but cannot create a file in that directory, and giving them write access to
# the service's state directory to save an upload would be the wrong trade. So
# the upload travels the same loopback hop everything else does.

def sanitize_voice_name(raw: str) -> str:
    """Reduce an operator-typed name to something that is safe as a filename.

    Substitution, not validation-then-use: after this there is no character
    left that could mean "parent directory" or "absolute path", so the result
    is safe by construction rather than by having passed a check somebody may
    later move or weaken. An empty result is the caller's problem to report.
    """
    return _VOICE_NAME_ALLOWED.sub("", raw or "").strip("-_")[:MAX_VOICE_NAME_CHARS]


def wav_duration_seconds(path: str) -> float:
    """Seconds of audio in a PCM wav, straight from its own header."""
    with wave.open(path, "rb") as fh:
        rate = fh.getframerate()
        if not rate:
            raise ValueError("wav header declares a zero sample rate")
        return fh.getnframes() / float(rate)


def convert_to_reference_wav(src: str, dst: str) -> None:
    """Whatever the operator recorded → 24 kHz mono 16-bit PCM wav.

    ffmpeg rather than a Python decoder because it is already installed on this
    host, it reads every container a phone or a browser produces (webm/opus
    from MediaRecorder, m4a from an iPhone, mp3, ogg), and it is the one piece
    of this path that does NOT need the model loaded — so voice management
    keeps working on a box where the GPU is sick.

    -nostdin: without it ffmpeg inherits the service's stdin and can block
    forever waiting on a prompt nobody will ever answer. -vn drops the cover
    art an mp3 may carry, which would otherwise turn into a video stream and
    fail the wav muxer.
    """
    if not shutil.which("ffmpeg"):
        raise HTTPException(
            status_code=503,
            detail="ffmpeg is not installed on the TTS host; cannot accept audio uploads",
        )
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", src,
         "-vn", "-ac", "1", "-ar", str(VOICE_SAMPLE_RATE),
         "-acodec", "pcm_s16le", "-f", "wav", dst],
        capture_output=True,
        # A conversion of a <=25 MB clip is a couple of seconds. A minute means
        # something is wrong, and a hung ffmpeg would hold a request thread and
        # a temp file for as long as the service lives.
        timeout=60,
    )
    if proc.returncode != 0:
        # ffmpeg's own last line says what it could not do far better than we
        # can guess, but the whole log is noise, so only the tail is surfaced.
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise HTTPException(
            status_code=400,
            detail="this file could not be read as audio"
                   + (f": {tail[-1]}" if tail else ""),
        )


def _store_reference_clip(payload: bytes, final: str) -> float:
    """Convert, measure, validate, publish. Returns the clip's duration.

    One scratch directory, removed whatever happens, so neither the raw upload
    nor a half-converted wav is left behind for the next admin to wonder at.
    """
    with tempfile.TemporaryDirectory(prefix="voice-upload-") as scratch:
        raw_path = os.path.join(scratch, "input")
        converted = os.path.join(scratch, "converted.wav")
        with open(raw_path, "wb") as fh:
            fh.write(payload)

        try:
            convert_to_reference_wav(raw_path, converted)
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="converting the audio took too long")

        try:
            seconds = wav_duration_seconds(converted)
        except Exception as exc:                   # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail=f"the converted audio is unreadable: {exc}",
            ) from exc

        if seconds < MIN_VOICE_SECONDS:
            raise HTTPException(
                status_code=400,
                detail=f"the clip is {seconds:.1f}s, too short to clone a voice from; "
                       f"record between {MIN_VOICE_SECONDS:.0f} and {MAX_VOICE_SECONDS:.0f} seconds "
                       f"(5-20 seconds works best)",
            )
        if seconds > MAX_VOICE_SECONDS:
            raise HTTPException(
                status_code=400,
                detail=f"the clip is {seconds:.1f}s, and anything longer than "
                       f"{MAX_VOICE_SECONDS:.0f}s is ignored by the model; trim it to 5-20 seconds",
            )

        # Same filesystem as the final path would be ideal for an atomic
        # rename, but /tmp may be a different mount, so copy then replace.
        staged = f"{final}.tmp"
        shutil.copyfile(converted, staged)
        os.replace(staged, final)
    return seconds


@app.post("/voices")
async def add_voice(name: str = Form(...), file: UploadFile = File(...)):
    """Store a reference clip for cloning, under `<sanitised name>.wav`.

    The order is convert → measure → validate → publish, deliberately:

      * measuring the CONVERTED file means the duration is read from a plain
        PCM wav header, so this needs no ffprobe and cannot be lied to by a
        crafted container's metadata;
      * publishing last, with os.replace, means a rejected upload leaves the
        previously working voice exactly as it was. An operator re-recording
        over a voice that is live in front of visitors cannot break it by
        submitting a bad take.
    """
    safe = sanitize_voice_name(name)
    if not safe:
        raise HTTPException(
            status_code=400,
            detail="the name must contain at least one English letter, digit, - or _",
        )

    payload = await file.read(MAX_VOICE_UPLOAD_BYTES + 1)
    if not payload:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")
    if len(payload) > MAX_VOICE_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"the file is larger than {MAX_VOICE_UPLOAD_BYTES // (1024 * 1024)} MB",
        )

    final = os.path.join(VOICES_DIR, f"{safe}.wav")
    replaced = os.path.exists(final)

    # Off the event loop. ffmpeg here has a 60-second timeout and the copy is
    # real disk work; doing it inline would freeze every other request on the
    # service, cache hits included, for as long as it took.
    seconds = await anyio.to_thread.run_sync(_store_reference_clip, payload, final)

    logger.info("voice %s: %s (%.1fs)", "replaced" if replaced else "added", safe, seconds)
    return {
        "name": safe,
        "seconds": round(seconds, 2),
        "sample_rate": VOICE_SAMPLE_RATE,
        "replaced": replaced,
    }


@app.delete("/voices/{name}")
def delete_voice(name: str):
    """Remove a reference clip.

    Cached audio previously generated with this voice is NOT swept: the entries
    are sha256 keys with no reverse index, and they are still perfectly valid
    audio. Re-using the name later is safe anyway — the cache key carries the
    new file's fingerprint, so a fresh clip never collides with the old one's
    cached output.
    """
    safe = sanitize_voice_name(name)
    path = os.path.join(VOICES_DIR, f"{safe}.wav")
    if not safe or not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"unknown voice: {name}")
    os.remove(path)
    logger.info("voice removed: %s", safe)
    return {"removed": safe}
