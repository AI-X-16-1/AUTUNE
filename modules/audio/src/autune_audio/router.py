"""HTTP entry point for module A.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/audio`` is applied by apps/api; declare paths relative to it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, UploadFile, status
from sqlalchemy.orm import Session

from autune_contracts.events import TRANSCRIPT_READY
from autune_contracts.transcript import Utterance
from autune_core import CurrentUser, get_logger, get_session
from autune_core.auth import clear_session_cookie
from autune_core.errors import AutuneError
from autune_core.events import publish
from autune_core.settings import get_settings as get_core_settings

from . import account, invitations, masking_rules, pii_report, service, storage
from .config import MAX_UPLOAD_BYTES
from .config import get_settings as get_audio_settings
from .enqueue import enqueue_process_recording
from .live.routes import router as live_router
from .persistence import transcript_payload
from .schemas import (
    AccountDeleted,
    ConsentAttestation,
    ConsentState,
    InvitationAccept,
    InvitationCreate,
    InvitationIssued,
    MaskingRule,
    MeetingCreate,
    MeetingDetail,
    MeetingState,
    MeetingSummary,
    MyData,
    PiiReport,
    PiiReported,
    SpeakerAssignment,
    SpeakerEntry,
    SpeechDeleted,
    TeamCreate,
    TeamMemberSummary,
    TeamPrivacy,
    TeamPrivacyUpdate,
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

router.include_router(live_router)


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


@router.post("/teams", response_model=TeamSummary, status_code=status.HTTP_201_CREATED)
def create_team(body: TeamCreate, user: CurrentUser, session: SessionDep) -> TeamSummary:
    """S02: a new workspace with the caller on it. See ``service.create_team``."""
    team = service.create_team(
        session,
        owner=user,
        name=body.name,
        role=body.role,
    )
    return TeamSummary(team_id=team.id, name=team.name)


@router.post(
    "/teams/{team_id}/invitations",
    response_model=InvitationIssued,
    status_code=status.HTTP_201_CREATED,
)
def invite_to_team(
    team_id: str,
    body: InvitationCreate,
    user: CurrentUser,
    session: SessionDep,
    response: Response,
) -> InvitationIssued:
    """Invite an address to a team the caller is on (#552). Answers with the
    link's token, once -- only its hash is kept -- and the same shape whatever
    the address. See ``invitations``."""
    token, expires_at = invitations.invite(session, team_id=team_id, email=body.email, by=user)
    # The token is a credential and this is its only appearance.
    response.headers["Cache-Control"] = "no-store"
    return InvitationIssued(token=token, expires_at=expires_at)


@router.post("/invitations/accept", response_model=TeamSummary)
def accept_invitation(
    body: InvitationAccept, user: CurrentUser, session: SessionDep
) -> TeamSummary:
    """Join the team an invitation link names, as the signed-in owner of the
    invited address. Every refusal is the same 404 (``InvitationUnusableError``)."""
    team = invitations.accept(session, token=body.token, user=user)
    return TeamSummary(team_id=team.id, name=team.name)


@router.get("/meetings", response_model=list[MeetingSummary])
def list_meetings(user: CurrentUser, session: SessionDep) -> list[MeetingSummary]:
    """The meetings of the teams this person belongs to, newest first. S05's list.

    Declared above ``/meetings/{meeting_id}`` so the literal path is read before
    the parameterised one. Starlette matches in declaration order and
    ``{meeting_id}`` never matches an empty segment, so the two cannot collide
    either way -- the order is for whoever reads the file next.

    The route takes ``CurrentUser`` and the service does the authorising — the
    same split as ``/transcripts/{id}`` — except that here the authorisation *is*
    the query, because there is no id in the request to check against. See
    ``service.meetings_for``.

    ``[]`` for somebody on no team, not a 404: no meetings is a state a new
    install is in, and the screen that renders it says so ("아직 회의가
    없습니다") rather than showing an error for a database that is merely empty.
    """
    return [
        MeetingSummary(
            meeting_id=meeting.id,
            title=meeting.title,
            status=meeting.status,
            started_at=meeting.started_at,
        )
        for meeting in service.meetings_for(session, member=user)
    ]


@router.get("/meetings/{meeting_id}", response_model=MeetingDetail)
def get_meeting(meeting_id: str, user: CurrentUser, session: SessionDep) -> MeetingDetail:
    """Where the meeting is in its life. Screen S12 polls this until it is
    ``complete`` or ``failed``, then reads the transcript. While a
    transcription runs, ``stage`` and ``stage_progress`` say how far it got
    (``progress.ProgressReporter``)."""
    meeting = service.meeting_for(session, meeting_id=meeting_id, reader=user)
    stage, stage_progress = service.running_stage(session, meeting_id=meeting.id)
    controls = service.transcription_controls(
        session, meeting=meeting, settings=get_audio_settings()
    )
    return MeetingDetail(
        meeting_id=meeting.id,
        title=meeting.title,
        status=meeting.status,
        original_audio_deleted=meeting.original_audio_deleted,
        pii_masked=meeting.pii_masked,
        team_id=meeting.team_id,
        stage=stage,
        stage_progress=stage_progress,
        **controls._asdict(),
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


@router.post("/meetings/{meeting_id}/transcription/cancel", response_model=MeetingState)
def cancel_transcription(meeting_id: str, user: CurrentUser, session: SessionDep) -> MeetingState:
    """Stop the meeting's transcription (S12 "처리 중단"). The meeting is
    ``failed`` on return and accepts a new upload; the worker stops within one
    heartbeat. 409 ``nothing_to_cancel`` when nothing is running."""
    meeting = service.cancel_transcription(
        session, meeting_id=meeting_id, user=user, settings=get_audio_settings()
    )
    session.commit()
    return MeetingState(meeting_id=meeting.id, status=meeting.status)


@router.post(
    "/meetings/{meeting_id}/transcription/restart",
    response_model=MeetingState,
    status_code=status.HTTP_202_ACCEPTED,
)
def restart_transcription(meeting_id: str, user: CurrentUser, session: SessionDep) -> MeetingState:
    """Run a stalled meeting again from its upload (S12 "다시 시작").

    409 ``not_stalled`` while the worker is alive, ``recording_gone`` when the
    upload is no longer on the server. Commit before the enqueue and fail the
    meeting if the broker refuses, exactly as ``upload_recording`` does; here
    there is no ``handover`` block to delete the file, so this does."""
    settings = get_audio_settings()
    job = service.restart_transcription(
        session, meeting_id=meeting_id, user=user, settings=settings
    )
    session.commit()
    try:
        enqueue_process_recording(job.id)
    except Exception as error:
        log.warning("audio_enqueue_failed", job_id=job.id, error=type(error).__name__)
        service.mark_failed(session, job_id=job.id)
        session.commit()
        storage.delete_orphan(storage.upload_path(job.id, settings))
        raise EnqueueFailedError() from error
    return MeetingState(meeting_id=job.meeting_id, status=job.meeting.status)


@router.get("/meetings/{meeting_id}/speakers", response_model=list[SpeakerEntry])
def list_speakers(meeting_id: str, user: CurrentUser, session: SessionDep) -> list[SpeakerEntry]:
    """The meeting's speakers, and who each one is or might be. What S13 and
    S15 draw next to an unidentified row."""
    return service.speakers_for(session, meeting_id=meeting_id, reader=user)


@router.get("/teams/{team_id}/members", response_model=list[TeamMemberSummary])
def list_team_members(
    team_id: str, user: CurrentUser, session: SessionDep
) -> list[TeamMemberSummary]:
    """The people the speaker picker can offer."""
    return service.members_of(session, team_id=team_id, reader=user)


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


@router.post(
    "/meetings/{meeting_id}/speakers/{speaker_label}", status_code=status.HTTP_204_NO_CONTENT
)
def assign_speaker(
    meeting_id: str,
    speaker_label: str,
    body: SpeakerAssignment,
    user: CurrentUser,
    session: SessionDep,
) -> None:
    """Confirm who a speaker is. The transcript then carries their
    ``speaker_id``, and the next meeting offers them as a candidate."""
    service.assign_speaker(
        session,
        meeting_id=meeting_id,
        speaker_label=speaker_label,
        user_id=body.user_id,
        confirmed_by=user,
    )


@router.delete("/me/voice-profile", status_code=status.HTTP_204_NO_CONTENT)
def delete_voice_profile(user: CurrentUser, session: SessionDep) -> None:
    """Delete every voice vector this account has confirmed."""
    service.delete_voice_profile(session, user=user)


@router.get("/me/data", response_model=MyData)
def my_data(user: CurrentUser, session: SessionDep) -> MyData:
    """S29 "내 데이터": counts of what Autune holds about the caller."""
    return account.my_data(session, user=user)


@router.get("/me/export")
def export_my_data(user: CurrentUser, response: Response, session: SessionDep) -> dict[str, object]:
    """S29 "내 데이터 내려받기 (JSON)". An attachment, so a browser saves it."""
    response.headers["Content-Disposition"] = 'attachment; filename="autune-my-data.json"'
    return account.export_my_data(session, user=user)


@router.delete("/me/speech", response_model=SpeechDeleted)
def delete_my_speech(user: CurrentUser, session: SessionDep) -> SpeechDeleted:
    """S29 "내 발화 데이터 모두 삭제": the caller's utterances and voice. The account stays."""
    return account.delete_my_speech(session, user=user)


@router.delete("/me", response_model=AccountDeleted)
def delete_account(user: CurrentUser, response: Response, session: SessionDep) -> AccountDeleted:
    """Delete the caller's account and everything that is theirs (#358).

    The session cookie is cleared in the same response: the token it carries
    names a user who no longer exists, and would answer 401 on every request.
    """
    deleted = account.delete_account(session, user=user)
    clear_session_cookie(response)
    return deleted


@router.get("/teams/{team_id}/privacy", response_model=TeamPrivacy)
def team_privacy(team_id: str, user: CurrentUser, session: SessionDep) -> TeamPrivacy:
    return account.team_privacy(session, team_id=team_id, reader=user)


@router.patch("/teams/{team_id}/privacy", response_model=TeamPrivacy)
def set_team_privacy(
    team_id: str, body: TeamPrivacyUpdate, user: CurrentUser, session: SessionDep
) -> TeamPrivacy:
    """S29's retention row. Applies to meetings held from now on."""
    return account.set_retention(session, team_id=team_id, days=body.retention_days, by=user)


# Statuses at which TranscriptReady has gone out. Before them nobody downstream
# holds the text, and the pipeline's own publish will carry the correction.
_ANNOUNCED = frozenset({"complete", "awaiting_confirmation", "delivered"})


@router.post(
    "/meetings/{meeting_id}/utterances/{utterance_id}/pii-report", response_model=PiiReported
)
def report_pii_miss(
    meeting_id: str,
    utterance_id: str,
    body: PiiReport,
    user: CurrentUser,
    session: SessionDep,
) -> PiiReported:
    """S30: mask a span the masker missed, then tell B, C and D (#555).

    **Committed before the publish**, as ``process_recording`` does: the event
    is a statement about what is stored, and a consumer that reads the rows
    after it must find the masked text. A failed publish does not undo the
    masking -- the stored text is corrected either way -- and is reported as
    ``republished: false`` rather than as an error the person would retry.
    """
    reported = pii_report.report_miss(
        session,
        meeting_id=meeting_id,
        utterance_id=utterance_id,
        start=body.start,
        end=body.end,
        category=body.category,
        include_similar=body.include_similar,
        reporter=user,
        add_rule=body.add_rule,
    )
    session.commit()

    republished = False
    meeting = service.meeting_for(session, meeting_id=meeting_id, reader=user)
    if meeting.status in _ANNOUNCED:
        try:
            payload = transcript_payload(session, meeting_id=meeting_id)
            publish(TRANSCRIPT_READY, payload.model_dump(mode="json"))
            republished = True
        except Exception as error:  # noqa: BLE001 -- the masking already landed
            log.warning(
                "audio_pii_republish_failed", meeting_id=meeting_id, error=type(error).__name__
            )
    return PiiReported(
        utterances=reported.utterances,
        occurrences=reported.occurrences,
        republished=republished,
        rule=reported.rule,
    )


@router.get("/teams/{team_id}/masking-rules", response_model=list[MaskingRule])
def list_masking_rules(team_id: str, user: CurrentUser, session: SessionDep) -> list[MaskingRule]:
    """S29's "추가 마스킹 항목": the shapes this team masks, learned from S30 reports."""
    return masking_rules.list_rules(session, team_id=team_id, reader=user)


@router.delete("/teams/{team_id}/masking-rules/{rule_id}", response_model=list[MaskingRule])
def delete_masking_rule(
    team_id: str, rule_id: int, user: CurrentUser, session: SessionDep
) -> list[MaskingRule]:
    """Stop masking one shape in later transcripts. Answers what remains."""
    masking_rules.delete_rule(session, team_id=team_id, rule_id=rule_id, by=user)
    return masking_rules.list_rules(session, team_id=team_id, reader=user)
