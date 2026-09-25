"""Private voice & audio transcription (pillar 6).

``TRANSCRIPTION_PROVIDER``:
* ``whisper_asr``    - self-hosted Whisper ASR web service (docker compose profile
                       ``voice``, image onerahmet/openai-whisper-asr-webservice) at
                       ``WHISPER_ENDPOINT``; audio never leaves the private network.
* ``faster_whisper`` - in-process local model (``pip install faster-whisper``).
* ``disabled``       - transcription endpoints return a setup hint.
"""
from __future__ import annotations

import asyncio
import tempfile

import httpx

from app.core.config import settings


class TranscriptionUnavailable(RuntimeError):
    pass


_model = None


def _faster_whisper(data: bytes, suffix: str) -> str:
    global _model
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionUnavailable("faster-whisper is not installed in this image") from exc
    if _model is None:
        _model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
    with tempfile.NamedTemporaryFile(suffix=suffix) as f:
        f.write(data)
        f.flush()
        segments, _ = _model.transcribe(f.name, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


async def transcribe(data: bytes, filename: str, content_type: str | None = None) -> str:
    provider = settings.transcription_provider
    if provider == "whisper_asr":
        try:
            async with httpx.AsyncClient(timeout=600) as client:
                resp = await client.post(f"{settings.whisper_endpoint.rstrip('/')}/asr", params={"task": "transcribe", "output": "json", "vad_filter": "true"},
                                         files={"audio_file": (filename, data, content_type or "application/octet-stream")})
                resp.raise_for_status()
                return (resp.json().get("text") or "").strip()
        except httpx.HTTPError as exc:
            raise TranscriptionUnavailable(f"Whisper service unreachable at {settings.whisper_endpoint}: {exc}") from exc
    if provider == "faster_whisper":
        suffix = "." + (filename.rsplit(".", 1)[-1] if "." in filename else "webm")
        return await asyncio.to_thread(_faster_whisper, data, suffix)
    raise TranscriptionUnavailable("Transcription is disabled. Set TRANSCRIPTION_PROVIDER=whisper_asr and start the 'voice' compose profile.")
