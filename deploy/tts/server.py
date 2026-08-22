"""Persian text-to-speech service for PadyarAIChatbot.

Runs Chatterbox (Thomcles/Chatterbox-TTS-Persian-Farsi fine-tune) on one
Tesla P40 and answers on 127.0.0.1:8003. Both chatbot installs call it over
loopback; it is never exposed to the internet.

THREE THINGS SHAPE THIS FILE, ALL OF THEM CONSEQUENCES OF THE HARDWARE:

1. float32 only. The P40's FP16 throughput is 1/64 of its FP32 throughput, so
   half precision is not an optimisation here — it is a slowdown.

2. One generation at a time. This is an autoregressive model on a 2016 card.
   Two concurrent requests do not halve latency, they double both. The lock is
   the honest representation of the hardware.

3. Disk cache, checked first. The chatbot's Tier-0/Tier-1 answers are fixed
   text from the dataset table, so the same string is synthesised over and
   over. Cached audio turns the common case into a file read, and /prerender
   lets the admin panel warm every dataset answer at save time — which is what
   makes a slow card acceptable in front of a live visitor.
"""
import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import numpy as np
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
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

_model = None
_model_error: Optional[str] = None

# Every generation runs on THIS one thread, never on the request handler's.
#
# FastAPI dispatches a sync endpoint to an anyio worker thread, and PyTorch's
# OpenMP team on such a thread came up far smaller than the configured 32 —
# measured at ~5 cores busy and RTF 19, against RTF 5.3 for identical text in a
# standalone process. Pinning generation to a single long-lived thread, whose
# thread count is set once from inside that thread, restores the standalone
# figure. max_workers=1 also enforces one-generation-at-a-time, which the
# hardware wants anyway, so this replaces the old lock rather than adding to it.
_generate_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-gen")
_threads_set = threading.Event()


def _apply_thread_config() -> int:
    """Run once, ON the generation thread — the setting does not travel."""
    if DEVICE == "cpu":
        torch.set_num_threads(CPU_THREADS)
    _threads_set.set()
    return torch.get_num_threads()


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

def load_model():
    global _model, _model_error
    if _model is not None:
        return _model
    from chatterbox import mtl_tts
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    # generate() validates language_id against this dict and raises for "fa".
    mtl_tts.SUPPORTED_LANGUAGES.setdefault("fa", "Persian")

    if DEVICE == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available to torch")
        # NOT a string match on get_arch_list(). The cu124 wheel ships sm_50,
        # sm_60, sm_70+ and NO sm_61 — yet it drives a P40 (sm_61) correctly,
        # because CUDA guarantees binary compatibility within one major
        # compute capability: sm_60 cubins run on sm_61. An earlier version of
        # this check refused to start on exactly the hardware it was written
        # for. The only honest test is to launch a kernel.
        cap = torch.cuda.get_device_capability(0)
        try:
            probe = torch.zeros(64, 64, device="cuda")
            (probe @ probe).sum().item()
            torch.cuda.synchronize()
        except Exception as exc:                  # noqa: BLE001
            raise RuntimeError(
                f"CUDA kernels will not run on {torch.cuda.get_device_name(0)} "
                f"(sm_{cap[0]}{cap[1]}) with torch {torch.__version__}: {exc}"
            ) from exc
        logger.info("device: %s (sm_%d%d), torch %s",
                    torch.cuda.get_device_name(0), cap[0], cap[1], torch.__version__)

    logger.info("loading Chatterbox from %s", MODEL_DIR)
    model = ChatterboxMultilingualTTS.from_local(MODEL_DIR, device=DEVICE)
    # float32 is deliberate — see the module docstring.
    logger.info("model ready, sample rate %s Hz", model.sr)
    _model = model
    _model_error = None
    return _model


@app.on_event("startup")
def _startup():
    global _model_error
    try:
        load_model()
    except Exception as exc:                      # noqa: BLE001
        # Do not crash-loop: /health must be able to report WHY it is down.
        _model_error = f"{type(exc).__name__}: {exc}"
        logger.error("model failed to load: %s", _model_error)


# --- audio -----------------------------------------------------------------

def to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    audio = np.clip(samples, -1.0, 1.0)
    pcm = (audio * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(pcm.tobytes())
    return buf.getvalue()


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
    # Two-level fan-out keeps the directory listing usable at scale.
    sub = os.path.join(CACHE_DIR, key[:2])
    os.makedirs(sub, exist_ok=True)
    return os.path.join(sub, f"{key}.wav")


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


def synthesize(text: str, voice: str, exaggeration: float, cfg_weight: float,
               language: str = LANGUAGE, temperature: float = 0.8) -> bytes:
    model = load_model()
    prompt = os.path.join(VOICES_DIR, f"{voice}.wav") if voice else ""
    if prompt and not os.path.exists(prompt):
        raise HTTPException(status_code=400, detail=f"unknown voice: {voice}")

    def _run() -> bytes:
        if not _threads_set.is_set():
            logger.info("generation thread using %d torch threads",
                        _apply_thread_config())
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
        return to_wav_bytes(np.concatenate(pieces), model.sr)

    # Blocks the request thread, which is correct: the caller wants the audio.
    audio = _generate_pool.submit(_run).result()

    seconds = (len(audio) - 44) / float(model.sr * 2)   # 16-bit mono
    if looks_truncated(text, seconds):
        logger.warning("truncated generation: %d chars produced %.2fs, retrying",
                       len(text), seconds)
        audio = _generate_pool.submit(_run).result()
        seconds = (len(audio) - 44) / float(model.sr * 2)
        if looks_truncated(text, seconds):
            # Refusing is the point. Returning it would cache a broken clip and
            # serve it to every visitor who asks that question afterwards.
            raise HTTPException(
                status_code=502,
                detail=(f"generation produced only {seconds:.1f}s of audio for "
                        f"{len(text)} characters, twice — refusing to cache it"))
    return audio


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


class PrerenderRequest(BaseModel):
    texts: List[str]
    voice: str = ""
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    temperature: float = 0.8


@app.get("/health")
def health():
    return {
        "status": "ok" if _model is not None else "degraded",
        "model_loaded": _model is not None,
        "error": _model_error,
        "device": DEVICE,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "sample_rate": getattr(_model, "sr", None),
        "language": LANGUAGE,
        "cpu_threads": CPU_THREADS if DEVICE == "cpu" else None,
    }


@app.post("/tts")
def tts(req: SpeakRequest):
    text = normalize(req.text)
    if not text:
        raise HTTPException(status_code=400, detail="text is empty after normalization")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail=f"text longer than {MAX_TEXT_CHARS} characters")

    key = cache_key(text, req.voice, req.exaggeration, req.cfg_weight, req.language,
                    req.temperature)
    path = cache_path(key)
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return Response(fh.read(), media_type="audio/wav",
                            headers={"X-TTS-Cache": "hit", "X-TTS-Key": key})

    audio = synthesize(text, req.voice, req.exaggeration, req.cfg_weight, req.language,
                       req.temperature)
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(audio)
    os.replace(tmp, path)                          # atomic: no half-written entry
    return Response(audio, media_type="audio/wav",
                    headers={"X-TTS-Cache": "miss", "X-TTS-Key": key})


@app.post("/prerender")
def prerender(req: PrerenderRequest):
    """Warm the cache for a batch of dataset answers.

    Called when an admin saves an entry, so the visitor-facing path is always
    a cache hit.
    """
    done, skipped, failed = [], [], []
    for raw in req.texts:
        text = normalize(raw)
        if not text:
            continue
        key = cache_key(text, req.voice, req.exaggeration, req.cfg_weight,
                        temperature=req.temperature)
        path = cache_path(key)
        if os.path.exists(path):
            skipped.append(key)
            continue
        try:
            audio = synthesize(text, req.voice, req.exaggeration, req.cfg_weight,
                               temperature=req.temperature)
            tmp = f"{path}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(audio)
            os.replace(tmp, path)
            done.append(key)
        except Exception as exc:                   # noqa: BLE001
            logger.error("prerender failed for %r: %s", text[:60], exc)
            failed.append({"text": text[:60], "error": str(exc)})
    return {"rendered": len(done), "cached_already": len(skipped), "failed": failed}


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

    # One scratch directory, removed whatever happens — neither the raw upload
    # nor a half-converted wav is left behind for the next admin to wonder at.
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
                detail=f"the clip is {seconds:.1f}s — too short to clone a voice from; "
                       f"record between {MIN_VOICE_SECONDS:.0f} and {MAX_VOICE_SECONDS:.0f} seconds "
                       f"(5-20 seconds works best)",
            )
        if seconds > MAX_VOICE_SECONDS:
            raise HTTPException(
                status_code=400,
                detail=f"the clip is {seconds:.1f}s — longer than {MAX_VOICE_SECONDS:.0f}s is "
                       f"ignored by the model; trim it to 5-20 seconds",
            )

        # Same filesystem as the final path would be ideal for an atomic
        # rename, but /tmp may be a different mount, so copy then replace.
        staged = f"{final}.tmp"
        shutil.copyfile(converted, staged)
        os.replace(staged, final)

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
