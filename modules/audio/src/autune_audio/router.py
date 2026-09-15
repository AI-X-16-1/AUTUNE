"""HTTP entry point for module A.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/audio`` is applied by apps/api; declare paths relative to it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from autune_core import CurrentUser, session_scope
from autune_core.settings import get_settings as get_core_settings

from . import service
from .schemas import RecordingAccepted
from .storage import stage_upload
from .tasks import process_recording

router = APIRouter()

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
"""Matches the dropzone limit on design screen S03.

Enforced by ``stage_upload`` while the bytes are written, not from
``UploadFile.size``: that is a number the client sent, and it is ``None`` on a
request without a Content-Length.
"""

# A local-only page for putting a recording through the pipeline by hand.
# It has no auth, so it is mounted nowhere but a developer's machine.
if get_core_settings().env == "local":
    from .dev import router as dev_router

    router.include_router(dev_router, prefix="/dev")


@router.get("/health")
def health() -> dict[str, str]:
    return {"module": "audio", "status": "ok"}


@router.post("/recordings", response_model=RecordingAccepted, status_code=202)
def upload_recording(
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    team_id: Annotated[str, Form()],
    title: Annotated[str, Form()],
) -> RecordingAccepted:
    """Take a recording, open a meeting for it, and queue the pipeline.

    **The order is the whole route.** Stage the bytes, commit the meeting, then
    queue — and each step exists where it does because of what its failure
    leaves behind.

    - *Stage first.* A meeting whose upload then fails is a row describing
      nothing. Writing the bytes first means a full disk or an oversized file is
      refused before anything is recorded.
    - *Commit before queueing.* The worker is handed a ``meeting_id``; queueing
      inside the transaction would hand it one a rollback then erased. This is
      the same reason ``tasks.process_recording`` publishes after its own commit
      rather than inside it.
    - *Delete the staged file if anything after staging fails.* Nothing will
      adopt it, and an unowned recording on disk is the one outcome invariant 11
      does not tolerate. ``sweep_stale_uploads`` is the net under the failures
      this cannot see, not a reason to skip the ones it can.

    The gap this cannot close: the queue call succeeds, the broker loses the
    task, and the meeting sits in ``analyzing`` with a file behind it until the
    sweep. Visible in the status, recoverable, and the honest cost of not doing
    transcription inside an HTTP request.

    Nothing here catches an ``AutuneError``. ``apps/api`` already turns one into
    a response carrying its own status — 413 for an oversized recording, 403 for
    a team the uploader is not in — and a second rendering here would be a
    second place those answers are decided. ``stage_upload`` deletes its own
    partial before raising, so a refused upload leaves nothing to clean up.

    **Two things about the upload this function does not control.** Starlette
    parses the multipart body before the first line here runs, and it spools a
    file part to a ``SpooledTemporaryFile`` in the system temp directory with no
    size limit of its own — so the whole body is already on disk, outside
    ``AUTUNE_AUDIO_TEMP_DIR`` and outside ``_reject_persistent``'s check, by the
    time ``MAX_UPLOAD_BYTES`` is counted. Starlette closes it when the request
    ends, so invariant 11 holds, but "counted while writing" is true of our copy
    only and a body-size limit belongs in front of the app.

    And ``delay`` hands the worker a **local path**, so the API process and the
    worker consuming ``gpu`` have to see the same filesystem. Split them across
    containers or hosts without a shared volume and ``adopt`` gets a path to
    nothing. See docs/engineering/environments.md.

    ``def`` rather than ``async def``: copying an upload to disk blocks, and
    Starlette runs a sync endpoint on a worker thread so it does not stall the
    event loop.
    """
    staged = stage_upload(
        file.file,
        suffix=Path(file.filename or "").suffix,
        max_bytes=MAX_UPLOAD_BYTES,
    )

    meeting_id: str | None = None
    try:
        with session_scope() as session:
            meeting = service.create_meeting(session, uploader=user, team_id=team_id, title=title)
            meeting_id, status = meeting.id, meeting.status
        process_recording.delay(meeting_id, str(staged))
    except BaseException:
        staged.unlink(missing_ok=True)
        if meeting_id is not None:
            # Committed by the block above, so it outlives this request unless
            # something says otherwise. A broker that refuses leaves a meeting
            # with no file and no task — harder to find than the file, because
            # the sweep covers the file and nothing covers the row.
            service.abandon_meeting(meeting_id)
        raise

    return RecordingAccepted(meeting_id=meeting_id, status=status)
