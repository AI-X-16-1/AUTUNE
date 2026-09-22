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

``PrivacyViolationError`` is the exception to that and is re-raised untouched.
``autune_core.errors`` says of it: "Never caught and downgraded. If this fires,
stop and fix the caller." Turning "the recording could not be deleted" into a
plain 500 would let the request finish looking ordinary, which is the opposite
of what the primitive raising it is for.

The endpoint is ``def`` rather than ``async def`` on purpose. Copying the upload
and then transcribing it are both blocking and the transcription runs for
minutes; Starlette gives a sync endpoint a worker thread, so neither stalls the
event loop.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from autune_audio.config import MAX_UPLOAD_BYTES
from autune_audio.decoding import DecodeError
from autune_audio.pipeline import transcribe_file
from autune_audio.storage import RecordingTooLargeError, recording_on_disk
from autune_core import Team, TeamMember, User, get_session
from autune_core.auth import issue_token
from autune_core.errors import PrivacyViolationError

from .page import PAGE

log = structlog.get_logger(__name__)

router = APIRouter()

# Not in the OpenAPI schema. These endpoints exist on a developer's machine and
# nowhere else, and the generated client should not know about them.


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def page() -> str:
    return PAGE


@router.post("/transcribe", include_in_schema=False)
def transcribe_upload(file: UploadFile) -> JSONResponse:
    """Decode, transcribe, and delete. The shape here is what page.py renders."""
    try:
        with recording_on_disk(
            file.file,
            suffix=Path(file.filename or "").suffix,
            max_bytes=MAX_UPLOAD_BYTES,
        ) as recording:
            started = time.monotonic()
            transcription = transcribe_file(recording.path)
            elapsed = time.monotonic() - started
    except PrivacyViolationError:
        # Never downgraded to a 500. See the module docstring.
        raise
    except RecordingTooLargeError as error:
        return JSONResponse(
            {"error": f"파일이 너무 큽니다. {error.limit_mb}MB 이하로 올려주세요."},
            status_code=error.status_code,
        )
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


# --- a token for the browser, until there is a sign-in ----------------------


class TokenRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    """Shape-checked only. ``EmailStr`` would pull in ``email-validator`` for a
    route that exists on one machine; ``users.email`` is ``String(320)``."""
    display_name: str = Field(default="", max_length=200)
    team_name: str = Field(default="Dev Team", min_length=1, max_length=200)


class TokenIssued(BaseModel):
    token: str
    user_id: str
    team_id: str


SessionDep = Annotated[Session, Depends(get_session)]


@router.post("/token", response_model=TokenIssued, include_in_schema=False)
def dev_token(body: TokenRequest, session: SessionDep) -> TokenIssued:
    """Name an email, get a person, a team, a membership and a bearer token.

    **The bridge between "every route takes ``CurrentUser``" and "there is no
    sign-in".** ``autune_core.auth`` has issued and verified JWTs since W1 so
    that routes could depend on ``current_user`` before screen S01 exists; what
    it never had was a caller. This is the caller, for a developer's machine.
    Real sign-in is #156 and #189, and this route is deleted the day it lands.

    Under ``/dev``, so mounted only when ``AUTUNE_ENV=local`` — the same gate as
    the upload page above. Off a developer's machine it does not exist, which
    is the whole of its security model, and why it must never move.

    **Idempotent on the email.** A second call returns the same user and the
    same team, so a page reload or a second developer does not fork the demo
    into two teams that cannot see each other's meetings. The team is looked up
    by the user's first membership, not by name: two developers naming the
    same team must land in the same one.

    Module A writes ``users`` and ``teams`` here. Invariant 4 lists both as
    A's to write, and nothing else in the repository creates either — the
    tests do, by hand, which is what this route replaces for a browser.
    """
    user = session.scalar(sa.select(User).where(User.email == body.email))
    if user is None:
        user = User(email=body.email, display_name=body.display_name or body.email.split("@")[0])
        session.add(user)
        session.flush()

    membership = session.scalar(
        sa.select(TeamMember).where(TeamMember.user_id == user.id).order_by(TeamMember.id)
    )
    if membership is None:
        team = session.scalar(sa.select(Team).where(Team.name == body.team_name))
        if team is None:
            team = Team(name=body.team_name)
            session.add(team)
            session.flush()
        membership = TeamMember(team_id=team.id, user_id=user.id)
        session.add(membership)
        session.flush()

    session.commit()
    # Ids only. An email is a person and a log line is a store.
    log.info("dev_token_issued", user_id=user.id, team_id=membership.team_id)
    return TokenIssued(token=issue_token(user.id), user_id=user.id, team_id=membership.team_id)
