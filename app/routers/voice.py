"""Voice input module — audio transcription via Whisper API."""

import asyncio

from fastapi import APIRouter, HTTPException, UploadFile, File

from app.config import logger, is_module_enabled
from app.db.queries import get_setting
from app.services.openai import _transcribe_sync, provider_config


router = APIRouter()


@router.post("/api/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)):
    """Transcribe uploaded audio to text using the configured STT model."""
    if not is_module_enabled("voice"):
        raise HTTPException(status_code=404, detail="Voice module is not enabled")

    # Fail closed: the admin toggle must hold even if a client keeps the
    # button visible or calls the endpoint directly.
    if get_setting('voice_enabled', 'true') != 'true':
        raise HTTPException(status_code=403, detail="Voice input is disabled")

    if not provider_config()[1]:
        raise HTTPException(status_code=500, detail="AI API key not configured")
    try:
        audio_bytes = await audio.read()
        filename = audio.filename or "recording.webm"
        content_type = audio.content_type or "unknown"

        logger.info(f"[Transcribe] Received upload: {len(audio_bytes)} bytes, filename={filename}, content_type={content_type}")
        logger.info(f"[Transcribe] First 20 bytes (hex): {audio_bytes[:20].hex()}")

        text = await asyncio.to_thread(_transcribe_sync, audio_bytes, filename)

        return {"text": text}
    except Exception as e:
        logger.error(f"[Transcribe] Endpoint failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")
