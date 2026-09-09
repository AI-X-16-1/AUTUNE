"""The dev endpoints. Registered only when the environment is local."""

from __future__ import annotations

import os
import time
from pathlib import Path
from tempfile import mkstemp

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import HTMLResponse

from autune_audio.decoding import DecodeError, decode
from autune_audio.dev.page import PAGE
from autune_audio.pipeline import transcribe
from autune_core import get_logger

log = get_logger(__name__)

router = APIRouter()

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
"""Matches the dropzone limit on S03."""

_UPLOAD = File(...)
"""FastAPI reads the marker from the default, and ruff objects to a call in a
default argument. Binding it once satisfies both."""


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def page() -> str:
    """A page for dropping a recording on and reading the transcript back."""
    return PAGE


@router.post("/transcribe", include_in_schema=False)
async def transcribe_upload(
    file: UploadFile = _UPLOAD,
    language: str | None = "ko",
) -> dict:
    """Decode and transcribe one upload, synchronously.

    The recording is written to a temp file because ffmpeg reads a path, and it
    is deleted in a ``finally`` block — on success, on failure, and on an
    exception raised while transcribing. The same rule as the real pipeline;
    see docs/architecture/privacy.md section 1.
    """
    body = await file.read()
    if len(body) > MAX_UPLOAD_BYTES:
        return {"error": f"파일이 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 를 넘습니다"}

    suffix = Path(file.filename or "upload").suffix or ".bin"
    handle, name = mkstemp(suffix=suffix)
    path = Path(name)
    try:
        with os.fdopen(handle, "wb") as temp:
            temp.write(body)

        started = time.perf_counter()
        waveform = decode(path)
        decoded_at = time.perf_counter()
        transcription = transcribe(waveform, language=language or None)
        finished = time.perf_counter()

        return {
            "duration": round(transcription.duration, 2),
            "language": transcription.language,
            "language_probability": round(transcription.language_probability, 3),
            "decode_seconds": round(decoded_at - started, 2),
            "transcribe_seconds": round(finished - decoded_at, 2),
            "realtime_factor": round((finished - started) / max(transcription.duration, 0.01), 2),
            "segments": [
                {
                    "start": round(s.start, 2),
                    "end": round(s.end, 2),
                    "text": s.text,
                    "words": [
                        {
                            "start": round(w.start, 2),
                            "end": round(w.end, 2),
                            "text": w.text,
                            "probability": round(w.probability, 2),
                        }
                        for w in s.words
                    ],
                }
                for s in transcription.segments
            ],
        }
    except DecodeError as exc:
        return {"error": exc.message}
    finally:
        # Success, failure, exception — the recording goes either way.
        path.unlink(missing_ok=True)
        log.info("dev_upload_deleted", existed=False)
