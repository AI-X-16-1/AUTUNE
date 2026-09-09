"""Serve the dev page and transcribe what it uploads.

The recording is written to a temp file because the decoder takes a path, and it
is deleted in a ``finally`` block — success, failure, and cancellation all delete
it. That is invariant 11, and it applies to a developer tool exactly as it
applies to the worker: there is no debug flag that keeps the audio.

Error strings are the exception's type and message. A decoder failure can carry
bytes of the file it was reading, so nothing derived from the file content is
returned to the browser or logged.
"""

from __future__ import annotations

import time
from pathlib import Path
from tempfile import NamedTemporaryFile

import structlog
from fastapi import APIRouter, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from autune_audio.config import get_settings
from autune_audio.pipeline import transcribe_file

from .page import PAGE

log = structlog.get_logger(__name__)

router = APIRouter()

# Whisper is the slow part, and a browser upload is not a job queue. Anything
# longer than this belongs in the Celery path, not in a page that blocks on it.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


@router.get("", response_class=HTMLResponse)
def page() -> str:
    return PAGE


@router.post("/transcribe")
async def transcribe_upload(file: UploadFile) -> JSONResponse:
    """Decode, transcribe, and delete. The shape here is what page.py renders."""
    settings = get_settings()
    temp_dir = Path(settings.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(file.filename or "").suffix
    with NamedTemporaryFile(dir=temp_dir, suffix=suffix, delete=False) as handle:
        path = Path(handle.name)

    try:
        size = 0
        with path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    limit = MAX_UPLOAD_BYTES // 1024 // 1024
                    return JSONResponse(
                        {"error": f"파일이 너무 큽니다. {limit}MB 이하로 올려주세요."},
                        status_code=413,
                    )
                out.write(chunk)

        started = time.monotonic()
        transcription = transcribe_file(path)
        elapsed = time.monotonic() - started
    except Exception as error:  # noqa: BLE001 - the browser gets a message, not a traceback
        log.warning("dev_transcribe_failed", error=type(error).__name__)
        return JSONResponse({"error": f"{type(error).__name__}: {error}"}, status_code=500)
    finally:
        # Invariant 11: the recording does not outlive the request.
        path.unlink(missing_ok=True)

    return JSONResponse(
        {
            "duration": round(transcription.duration, 1),
            "transcribe_seconds": round(elapsed, 1),
            "realtime_factor": round(elapsed / transcription.duration, 2)
            if transcription.duration
            else 0.0,
            "language": transcription.language,
            "language_probability": round(transcription.language_probability, 4),
            "segments": [
                {
                    "start": round(segment.start, 2),
                    "end": round(segment.end, 2),
                    "text": segment.text,
                    "words": [
                        {
                            "start": round(word.start, 2),
                            "end": round(word.end, 2),
                            "text": word.text,
                            "probability": round(word.probability, 2),
                        }
                        for word in segment.words
                    ],
                }
                for segment in transcription.segments
            ],
        }
    )
