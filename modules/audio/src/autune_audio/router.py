"""HTTP entry point for module A.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/audio`` is applied by apps/api; declare paths relative to it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile, status
from sqlalchemy.orm import Session

from autune_contracts.transcript import Utterance
from autune_core import CurrentUser, get_logger, get_session
from autune_core.errors import AutuneError
from autune_core.settings import get_settings as get_core_settings

from . import service
from .config import MAX_UPLOAD_BYTES
from .config import get_settings as get_audio_settings
from .enqueue import enqueue_process_recording
from .schemas import (
    ConsentAttestation,
    ConsentState,
    MeetingCreate,
    MeetingDetail,
    MeetingState,
    TeamSummary,
)
from .storage import assign, handover

log = get_logger(__name__)


class EnqueueFailedError(AutuneError):
    """The recording was accepted, written, and then could not be queued.

    A 500 because it is ours, not the caller's: the file was fine and the
    meeting was theirs to record. The recording has already been deleted and the
    meeting marked failed by the time this is raised, so retrying the upload is
    the right thing for the caller to do — hence a message that says so.
    """

    code = "enqueue_failed"
    status_code = 500

    def __init__(self) -> None:
        super().__init__(
            "the recording could not be queued for transcription and was deleted; "
            "please upload it again"
        )


router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]

# A local-only page for putting a recording through the pipeline by hand.
# It has no auth, so it is mounted nowhere but a developer's machine.
if get_core_settings().env == "local":
    from .dev import router as dev_router

    router.include_router(dev_router, prefix="/dev")


@router.get("/health")
def health() -> dict[str, str]:
    return {"module": "audio", "status": "ok"}


@router.get("/transcripts/{meeting_id}", response_model=list[Utterance])
def get_transcript(meeting_id: str, user: CurrentUser, session: SessionDep) -> list[Utterance]:
    """This meeting's transcript, masked, for a member of its team.

    **The route takes `CurrentUser` and the service checks the team.** A meeting
    transcript is the most sensitive thing this repository stores, and a token
    alone says only that somebody is signed in. #156 and #189 are open about the
    rest of the routes; this one does not wait for them.

    `list[Utterance]`, not `TranscriptReady`. That payload is the announcement A
    publishes once when a meeting is over, and a consumer is required to check
    `privacy.original_audio_deleted` before touching it. A screen reading a
    stored transcript is not that consumer and should not be handed something
    that looks like the event.
    """
    return service.transcript_for_meeting(session, meeting_id=meeting_id, reader=user)


@router.get("/teams", response_model=list[TeamSummary])
def list_teams(user: CurrentUser, session: SessionDep) -> list[TeamSummary]:
    """The teams this person may open a meeting for. Feeds ``POST /meetings``."""
    return [
        TeamSummary(team_id=team.id, name=team.name)
        for team in service.teams_for(session, member=user)
    ]


@router.get("/meetings/{meeting_id}", response_model=MeetingDetail)
def get_meeting(meeting_id: str, user: CurrentUser, session: SessionDep) -> MeetingDetail:
    """Where the meeting is in its life. Screen S12 polls this until it is
    ``complete`` or ``failed``, then reads the transcript."""
    meeting = service.meeting_for(session, meeting_id=meeting_id, reader=user)
    return MeetingDetail(
        meeting_id=meeting.id,
        title=meeting.title,
        status=meeting.status,
        original_audio_deleted=meeting.original_audio_deleted,
        pii_masked=meeting.pii_masked,
    )


@router.post("/meetings", response_model=MeetingState, status_code=status.HTTP_201_CREATED)
def create_meeting(body: MeetingCreate, user: CurrentUser, session: SessionDep) -> MeetingState:
    """Open a meeting, before there is any audio for it.

    Separate from the upload below because the live-microphone path needs a
    meeting before it has a recording, and because ``meetings`` is a shared
    entity only module A may write — one writer, one place.
    """
    meeting = service.create_meeting(
        session,
        owner=user,
        title=body.title,
        team_id=body.team_id,
        started_at=body.started_at,
    )
    return MeetingState(meeting_id=meeting.id, status=meeting.status)


@router.post(
    "/meetings/{meeting_id}/recording",
    response_model=MeetingState,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_recording(
    meeting_id: str, file: UploadFile, user: CurrentUser, session: SessionDep
) -> MeetingState:
    """Take a recording, hand it to the worker, and return before it is done.

    **202, not 200.** Transcription runs at roughly real time on CPU, so a
    meeting is minutes of work and the response says "queued", not "done". The
    screen follows the meeting's status from here (S12).

    **The file is still on disk when this returns, and that is the design.**
    ``handover`` writes it and deletes it only if this block fails; on success
    the worker adopts it and deletion becomes its ``finally``. Exactly one of
    the two owns the file at any moment (privacy.md section 1: owned by exactly
    one party at a time).

    **The worker is told the job, never the path.** The file is renamed to
    ``{job_id}.upload`` once the claim has produced a job, and the queue
    carries the id; the worker rebuilds the path from it. A path in a Celery
    payload is the thing section 1 forbids by name, because Celery writes task
    arguments to the broker and to its failure output (#275).

    **Everything that can refuse the upload runs inside that block.** The
    authorisation check and the status claim look like they belong before the
    bytes are written, and they cannot be: the body is streaming while the
    request is read, so by the time a route function runs there is already a
    file. Putting the refusals inside ``handover`` is what deletes it on the way
    out. The order within the block is still cheapest-first — the claim, then
    the queue — so a 403 never reaches the broker.

    **The status flip commits before the enqueue is attempted**, so the worker
    cannot pick the meeting up and find it still ``scheduled``. If the enqueue
    then fails, ``mark_failed`` puts the meeting somewhere a person can see,
    rather than leaving it ``analyzing`` for a task that does not exist.

    **Only the enqueue may fail the meeting.** The ``except`` is around that
    one call and nothing else, because the three ways to get here mean three
    different things: a disk that would not take the bytes is not the
    meeting's fault and leaves it ``scheduled``; a refusal (403, 404, 409, 413)
    is a meeting already in the state it should be in; and only a broker that
    would not take the task leaves a claimed meeting with nobody coming for
    it. The first version of this route wrapped all three in one handler and
    would have failed a meeting over a full disk.
    """
    with handover(
        file.file,
        max_bytes=MAX_UPLOAD_BYTES,
        settings=get_audio_settings(),
    ) as recording:
        job = service.start_transcription(session, meeting_id=meeting_id, uploader=user)
        # The file takes the job's name and the queue takes the job's id. No
        # path leaves this process (privacy.md section 1).
        assign(recording, job.id)
        session.commit()
        try:
            enqueue_process_recording(job.id)
        except Exception as error:
            # Raising inside the block is what makes handover delete the file.
            log.warning("audio_enqueue_failed", job_id=job.id, error=type(error).__name__)
            service.mark_failed(session, job_id=job.id)
            session.commit()
            raise EnqueueFailedError() from error

    return MeetingState(meeting_id=job.meeting_id, status=job.meeting.status)


@router.post("/meetings/{meeting_id}/consent", response_model=ConsentState)
def attest_consent(
    meeting_id: str, body: ConsentAttestation, user: CurrentUser, session: SessionDep
) -> ConsentState:
    """A member states that everyone in this meeting's recording consented.

    Its own route rather than a checkbox on the upload, on purpose. "Everyone
    consented" is a statement with legal weight, and it is made by pressing one
    thing that means only that -- not by a box in the corner of a form whose
    main job is a file. It is also what lets this land independently of the
    upload route (#259): a meeting exists, somebody attests, and the next
    transcript written for it carries the consent.

    ``body.attested`` can only be ``true``. See ``ConsentAttestation``.

    This is the only writer of ``participants.consented = True`` in the
    repository, and it is per meeting because per person is not possible before
    identification (#6). When S10's per-attendee table exists, this route is
    derived from it or removed -- #190.
    """
    service.attest_consent(session, meeting_id=meeting_id, attested_by=user)
    session.commit()
    return ConsentState(meeting_id=meeting_id, attested=True)
