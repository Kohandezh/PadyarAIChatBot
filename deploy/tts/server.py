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
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
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


def cache_key(text: str, voice: str, exaggeration: float, cfg_weight: float,
              language: str = LANGUAGE) -> str:
    raw = "\x00".join([text, voice, f"{exaggeration:.3f}", f"{cfg_weight:.3f}", language])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_path(key: str) -> str:
    # Two-level fan-out keeps the directory listing usable at scale.
    sub = os.path.join(CACHE_DIR, key[:2])
    os.makedirs(sub, exist_ok=True)
    return os.path.join(sub, f"{key}.wav")


def synthesize(text: str, voice: str, exaggeration: float, cfg_weight: float,
               language: str = LANGUAGE) -> bytes:
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
            kwargs = {"exaggeration": exaggeration, "cfg_weight": cfg_weight}
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
    return _generate_pool.submit(_run).result()


# --- API -------------------------------------------------------------------

class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1)
    voice: str = Field("", description="Filename stem under the voices dir; empty = model default")
    exaggeration: float = Field(0.5, ge=0.0, le=2.0)
    cfg_weight: float = Field(0.5, ge=0.0, le=1.0)
    language: str = Field(LANGUAGE, description="language_id passed to the model")


class PrerenderRequest(BaseModel):
    texts: List[str]
    voice: str = ""
    exaggeration: float = 0.5
    cfg_weight: float = 0.5


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

    key = cache_key(text, req.voice, req.exaggeration, req.cfg_weight, req.language)
    path = cache_path(key)
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return Response(fh.read(), media_type="audio/wav",
                            headers={"X-TTS-Cache": "hit", "X-TTS-Key": key})

    audio = synthesize(text, req.voice, req.exaggeration, req.cfg_weight, req.language)
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
        key = cache_key(text, req.voice, req.exaggeration, req.cfg_weight)
        path = cache_path(key)
        if os.path.exists(path):
            skipped.append(key)
            continue
        try:
            audio = synthesize(text, req.voice, req.exaggeration, req.cfg_weight)
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
