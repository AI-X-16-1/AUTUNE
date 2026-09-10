"""Serve the dev page and transcribe what it uploads.

The recording goes to disk through ``storage.recording_on_disk``, the same
primitive the worker uses. Invariant 11 applies to a developer tool exactly as
it applies to the worker — there is no debug flag that keeps the audio — and a
second hand-written ``finally`` here would be a second place to get it wrong.

One copy is outside that guarantee and worth naming: Starlette spools an upload
over 1 MB to its own temp file before this function is entered, in the system
temp directory rather than ``AUTUNE_AUDIO_TEMP_DIR``. It is removed when the
request ends, so it does not outlive the task, but ``_reject_persistent`` never
sees it. The real pipeline does not have this copy — the worker is handed a path
by the upload endpoint, not a multipart body.

A ``DecodeError`` carries a message written to name the file and never its
contents, so it is safe to show. Anything else is reported by exception type
alone: ffmpeg's stderr can quote bytes of what it was reading, and that must not
reach the browser or the log.
"""

from __future__ import annotations

import time
from pathlib import Path

import structlog
from fastapi import APIRouter, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from autune_audio.decoding import DecodeError
from autune_audio.pipeline import transcribe_file
from autune_audio.storage import recording_on_disk

from .page import PAGE

log = structlog.get_logger(__name__)

router = APIRouter()

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
"""Matches the dropzone limit on design screen S03."""

# Not in the OpenAPI schema. These endpoints exist on a developer's machine and
# nowhere else, and the generated client should not know about them.


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def page() -> str:
    return PAGE


@router.post("/transcribe", include_in_schema=False)
async def transcribe_upload(file: UploadFile) -> JSONResponse:
    """Decode, transcribe, and delete. The shape here is what page.py renders."""
    # Checked from the declared size rather than by reading: a body over the
    # limit is refused without this endpoint having copied it anywhere.
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        limit = MAX_UPLOAD_BYTES // 1024 // 1024
        return JSONResponse(
            {"error": f"파일이 너무 큽니다. {limit}MB 이하로 올려주세요."},
            status_code=413,
        )

    try:
        with recording_on_disk(file.file, suffix=Path(file.filename or "").suffix) as recording:
            started = time.monotonic()
            transcription = transcribe_file(recording.path)
            elapsed = time.monotonic() - started
    except DecodeError as error:
        # Written to name the file rather than quote it; safe to show.
        log.warning("dev_decode_failed", code=error.code)
        return JSONResponse({"error": error.message}, status_code=error.status_code)
    except Exception as error:  # noqa: BLE001 - the browser gets a type, not a traceback
        # Never the message: it can carry bytes of the recording.
        kind = type(error).__name__
        log.warning("dev_transcribe_failed", error=kind)
        return JSONResponse({"error": f"전사에 실패했습니다 ({kind})"}, status_code=500)

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
