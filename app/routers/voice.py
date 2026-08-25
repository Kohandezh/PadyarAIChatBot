"""Voice input module — audio transcription via Whisper API."""

import asyncio
import os

from fastapi import APIRouter, HTTPException, Request, UploadFile, File

from app.auth import security
from app.auth.security import (
    client_ip, validate_chat_token, validate_request_origin,
)
from app.config import logger, is_module_enabled
from app.db.queries import get_setting
from app.services.openai import _transcribe_sync, provider_config


router = APIRouter()

# Whisper-style providers reject audio above ~25 MB anyway; enforcing it here
# also caps how much memory an unauthenticated caller can pin per request.
MAX_AUDIO_BYTES = 25 * 1024 * 1024

# What the mic on every supported browser records. Anything else (an .exe, a
# PDF, a renamed file) is forwarded verbatim to the paid provider today, which
# is a free-relay bug, not a feature.
_ALLOWED_AUDIO_EXTS = {".webm", ".ogg", ".oga", ".mp3", ".mp4", ".m4a",
                       ".wav", ".flac"}


@router.post("/api/transcribe")
async def transcribe_audio(request: Request, audio: UploadFile = File(...)):
    """Transcribe uploaded audio to text using the configured STT model.

    Same guard trio as /chat (origin + HMAC token + rate limit): this endpoint
    spends the install owner's STT credits, so it must never be an open relay.

    The guards are in-handler sequential calls (not router dependencies), in
    the same order the dependencies ran — origin → token → limit — and sit
    before every other check, so their 403/429 keep today's precedence over
    the module 404 / toggle 403 / provider 500 below. In-handler because the
    token check RETURNS the visitor identity the two-tier limiter needs:
    transcribe draws from the SAME chat:{nonce} + chatip:{ip} buckets as
    /chat, so one visitor has ONE budget across text and voice and cannot
    double an address's traffic by alternating surfaces.
    """
    validate_request_origin(request)
    nonce = validate_chat_token(request)
    ip = client_ip(request) or "unknown"
    security.check_rate_limits(request, [
        (f"chat:{nonce or 'ip:' + ip}", security.CHAT_RATE_LIMIT),
        (f"chatip:{ip}", security.CHAT_IP_RATE_LIMIT),
    ])

    if not is_module_enabled("voice"):
        raise HTTPException(status_code=404, detail="Voice module is not enabled")

    # Fail closed: the admin toggle must hold even if a client keeps the
    # button visible or calls the endpoint directly.
    if get_setting('voice_enabled', 'true') != 'true':
        raise HTTPException(status_code=403, detail="Voice input is disabled")

    if not provider_config()[1]:
        raise HTTPException(status_code=500, detail="AI API key not configured")

    filename = audio.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    content_type = (audio.content_type or "").split(";")[0].strip().lower()
    if (ext and ext not in _ALLOWED_AUDIO_EXTS
            and not content_type.startswith("audio/")):
        raise HTTPException(status_code=400, detail="Only audio files can be transcribed")

    try:
        # Read at most one byte beyond the cap: a chunked body with no
        # Content-Length is caught here, not by the size middleware.
        audio_bytes = await audio.read(MAX_AUDIO_BYTES + 1)
        if len(audio_bytes) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Audio file is too large")
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="Audio file is empty")

        # Basename + printable-only: the name is logged and forwarded to the
        # provider, so a crafted name must not smuggle path segments or log
        # line-breaks.
        safe_name = os.path.basename(filename) or "recording.webm"
        safe_name = "".join(c for c in safe_name if c.isprintable())[:120] or "recording.webm"
        logger.info("[Transcribe] Received upload: %d bytes, content_type=%s",
                    len(audio_bytes), content_type or "unknown")

        text = await asyncio.to_thread(_transcribe_sync, audio_bytes, safe_name)

        return {"text": text}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        # Full detail stays in the server log only — provider errors can carry
        # base URLs, request ids and account hints an anonymous caller must
        # never see.
        logger.error(f"[Transcribe] Endpoint failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Transcription failed")
