"""Business logic for module A: Audio Pipeline.

Owner: 김민경. See docs/modules/audio.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``aud_*`` tables.
Never imports another module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts.transcript import Utterance as ContractUtterance
from autune_core import Meeting, Participant, Team, TeamMember, User, get_logger
from autune_core.errors import ConflictError, NotFoundError, PermissionDeniedError

from . import storage
from .config import AudioSettings
from .models import AudConsentAttestation, TranscriptionJob
from .persistence import transcript_payload

log = get_logger(__name__)


def require_team_member(session: Session, *, user_id: str, team_id: str) -> None:
    """Raise unless ``user_id`` belongs to ``team_id``.

    A token proves who is asking, not which team's meetings they may read. Named
    and shared rather than written inline, because every route this module grows
    asks the same question and an authorisation check that exists in two places
    is one that can come to mean two things.
    """
    member = session.scalar(
        sa.select(TeamMember.id).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    )
    if member is None:
        raise PermissionDeniedError("you are not a member of this team")


def transcript_for_meeting(
    session: Session, *, meeting_id: str, reader: User
) -> list[ContractUtterance]:
    """One meeting's transcript, for the person asking to read it.

    **The same builder the published event uses.** ``transcript_payload`` shapes
    what goes out on ``TranscriptReady``; taking its ``utterances`` rather than
    querying the rows again means the screen and the four consuming modules
    cannot come to disagree about what was said, or about the order it was said
    in. Two readers of the same rows is how the masker and the outbound guard
    drifted apart in #126.

    **Masked, because that is what is stored.** There is no unmasked column to
    read and no flag that returns one — module A masks before the first write
    (privacy.md section 2) — so this returns what the database has and the screen
    marks the masked spans rather than offering to reveal them.

    **Empty while the task is still running**, which is honest rather than a
    special case: ``persist_transcript`` writes every utterance in one
    transaction at the end, so a meeting part-way through has none rather than
    some. A caller that needs to tell "not yet" from "nothing was said" reads
    the meeting's status, which is what it is for.

    Speaking ratios are not here and never will be. privacy.md section 3 gives
    those to module E, delivered to the speaker and nobody else.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=reader.id, team_id=meeting.team_id)

    # Ids only. A transcript is meeting content and a log line is a store.
    log.info("audio_transcript_read", meeting_id=meeting_id, reader_id=reader.id)
    return transcript_payload(session, meeting_id=meeting_id).utterances


_ACCEPTS_A_RECORDING = frozenset({"scheduled", "failed"})
"""Meeting statuses a recording may be submitted for.

The other five are refusals with different reasons: ``recording`` and
``analyzing`` already have a run in flight, and ``awaiting_confirmation``,
``complete`` and ``delivered`` have a transcript four modules have already been
told about.
"""


def create_meeting(
    session: Session,
    *,
    owner: User,
    title: str,
    team_id: str,
    started_at: datetime | None = None,
) -> Meeting:
    """Open a meeting for ``team_id``, before there is any audio.

    **Module A creates it because module A owns it.** ``meetings`` is a shared
    entity and invariant 4 gives A the only write — so the row cannot be made by
    whoever happens to need it, and until now nothing made it at all: the table
    had no writer outside the tests and the pipeline had no front door.

    Separate from the upload on purpose. The live-microphone path (S10/S13) has
    a meeting well before it has a recording, and a scheduled meeting exists
    before anybody presses anything. Folding creation into the upload would give
    that path its own second way to make a row, and two writers of a shared
    entity is how the column that means one thing here comes to mean another
    there.

    ``status`` starts at ``scheduled`` — the column's default, written out here
    because the lifecycle in this module reads better when every transition is
    visible in one file.

    **``expires_at`` is set here, from the team's retention window.** Nothing
    else in the repository writes it (#206), and module D reads
    ``expires_at IS NULL`` as "never expires" — so a meeting created without it
    is one the retention sweep and every retention-aware read ignore for good.
    Resolved now rather than at read time: a team that later shortens its
    retention does not retroactively un-record what was agreed.
    """
    require_team_member(session, user_id=owner.id, team_id=team_id)
    team = session.get(Team, team_id)
    if team is None:  # membership just passed, so the team exists; this is a torn read
        raise NotFoundError("team", team_id)

    meeting = Meeting(
        team_id=team_id,
        title=title,
        started_at=started_at,
        status="scheduled",
        expires_at=datetime.now(tz=UTC) + timedelta(days=team.retention_days),
    )
    session.add(meeting)
    session.flush()

    # The title is the team's own words and can carry a client name; it is not
    # logged. The id is enough to follow the meeting through the pipeline.
    log.info("audio_meeting_created", meeting_id=meeting.id, team_id=team_id, owner_id=owner.id)
    return meeting


def start_transcription(session: Session, *, meeting_id: str, uploader: User) -> TranscriptionJob:
    """Claim the meeting for a recording that is about to be queued.

    **The status flip is the claim, and it has to happen before the enqueue.**
    ``persist_transcript`` replaces a meeting's utterances rather than appending
    — that is what makes the task safe to redeliver — so two recordings running
    against one meeting do not merge, they race, and the meeting keeps whichever
    finished last with no trace that the other existed. Refusing here is how
    that stops being possible.

    ``ConflictError`` rather than a silent second queue: the caller uploaded a
    file and is entitled to know it was not accepted.

    ``failed`` is accepted alongside ``scheduled`` because it is the state a
    recovery starts from — a decoder that fell over or an enqueue that never
    reached the broker leaves a meeting with no transcript and no task, and the
    alternative to retrying it is abandoning the meeting row and everything
    attached to it. ``complete`` is not accepted: that transcript has already
    gone out to four modules, and replacing it underneath them is the rerun
    problem in #194 rather than something an upload decides on its own.

    **Returns the attempt, not the meeting.** The job row is what the worker
    is queued -- an opaque id, never a path (privacy.md section 1) -- and what
    the recording is named after. Any earlier attempt for this meeting still
    ``queued`` or ``running`` is marked ``superseded`` so that, should it turn
    up late, the worker declines it rather than racing this one.

    **The row is locked for the read.** Two uploads finishing together -- a
    double click, a retry -- would both read ``scheduled`` and both queue, and
    the meeting would be transcribed twice and announced twice with different
    ``utt_`` ids, which breaks D's lineage (@kjfcvx12 on #259, #194). With the
    lock the second waits, reads ``analyzing``, and gets the 409 it should.
    """
    meeting = session.get(Meeting, meeting_id, with_for_update=True)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=uploader.id, team_id=meeting.team_id)

    if meeting.status not in _ACCEPTS_A_RECORDING:
        raise ConflictError(
            f"meeting {meeting_id} is {meeting.status}; a recording can only be "
            f"submitted for a meeting that is {' or '.join(sorted(_ACCEPTS_A_RECORDING))}"
        )

    now = datetime.now(tz=UTC)
    for stale in session.scalars(
        sa.select(TranscriptionJob).where(
            TranscriptionJob.meeting_id == meeting_id,
            TranscriptionJob.status.in_(("queued", "running")),
        )
    ):
        stale.status = "superseded"
        stale.finished_at = now
        log.info("audio_job_superseded", job_id=stale.id, meeting_id=meeting_id)

    job = TranscriptionJob(meeting_id=meeting_id, status="queued")
    session.add(job)
    meeting.status = "analyzing"
    session.flush()
    log.info(
        "audio_transcription_started",
        meeting_id=meeting_id,
        job_id=job.id,
        uploader_id=uploader.id,
    )
    return job


class Claim(NamedTuple):
    """What the worker learned at the door. See ``claim_job``."""

    meeting_id: str
    run: bool
    """This attempt is current and now ``running``; go ahead."""
    owns_file: bool
    """Nobody else will delete this attempt's upload; the caller must."""


def claim_job(session: Session, *, job_id: str) -> Claim:
    """The worker's first line: which meeting, whether to run, and who has the file.

    A ``queued`` job becomes ``running`` and is run. Anything else is declined:

    - ``running`` -- a redelivery while the first delivery is still going
      (``acks_late`` and Redis' one-hour visibility timeout make that a long
      meeting, not a fault). The first delivery owns the file and will delete
      it in its ``finally``; this one must not touch it.
    - ``done`` / ``failed`` -- a redelivery after the attempt finished. The
      file is already gone; ``owns_file`` is True so the caller's delete is a
      no-op that confirms it.
    - ``superseded`` -- a later upload for the same meeting was accepted
      first. This attempt's file is still there and nobody else will delete
      it, so the caller does. Running it would race the current attempt.

    ``mark_failed`` is not called for a declined job because nothing about the
    *meeting* failed. Raises ``NotFoundError`` for an id nobody queued: a
    broker carrying a message this database has no record of should be loud.
    """
    job = session.get(TranscriptionJob, job_id)
    if job is None:
        raise NotFoundError("job", job_id)
    if job.status != "queued":
        log.info("audio_job_declined", job_id=job_id, status=job.status)
        return Claim(job.meeting_id, run=False, owns_file=job.status != "running")
    job.status = "running"
    session.flush()
    return Claim(job.meeting_id, run=True, owns_file=True)


def mark_failed(session: Session, *, job_id: str) -> None:
    """Record that this attempt will not finish, and the meeting with it.

    Takes no user. Both callers are places where there is nobody to authorise
    against: the worker, whose task has just raised, and the endpoint, whose
    enqueue did not reach the broker. An authorisation check here would either
    be skipped or be given a fake user to satisfy it, and both are worse than
    not having one — the meeting was already claimed by a request that *was*
    checked.

    A meeting that stayed ``analyzing`` forever would be indistinguishable from
    one still being transcribed, and the screen would spin on it for good.

    **A superseded or finished job does not touch the meeting.** The meeting's
    status belongs to its current attempt. A first attempt that turns up late,
    dies, and marks ``failed`` the meeting a second attempt is busy with was
    the race #275 was opened over; keying failure on the job rather than the
    meeting is what closes it.

    **Only the current attempt can fail the meeting, and only from
    ``analyzing`` -- or from ``complete`` when its own publish failed.**
    ``failed`` means "will not reach the four consumers". A redelivery of a
    finished job never gets here: ``claim_job`` declines it, so the case
    that used to matter -- a worker dying after the commit and the publish,
    the redelivered run failing at decode, the meeting turning red over a
    transcript four modules already hold -- cannot reach this function. What
    can is a publish that failed after the commit, and that meeting *should*
    go red: nobody was told, and ``failed`` is what lets a re-upload in.
    """
    job = session.get(TranscriptionJob, job_id)
    if job is None:
        raise NotFoundError("job", job_id)

    if job.status not in ("queued", "running"):
        log.info("audio_job_failed_skipped", job_id=job_id, status=job.status)
        return

    job.status = "failed"
    job.finished_at = datetime.now(tz=UTC)

    meeting = session.get(Meeting, job.meeting_id)
    if meeting is not None and meeting.status in ("analyzing", "complete"):
        # ``complete`` only reaches here from a publish that failed after the
        # commit: this job is still ``running`` (a redelivery of a done job
        # never gets this far -- ``claim_job`` declines it), so the meeting
        # was completed by *this* attempt and nobody was told.
        meeting.status = "failed"
        log.info("audio_meeting_failed", meeting_id=meeting.id, job_id=job_id)
    else:
        log.info(
            "audio_meeting_failed_skipped",
            meeting_id=job.meeting_id,
            job_id=job_id,
            status=None if meeting is None else meeting.status,
        )
    session.flush()


def mark_complete(session: Session, *, job_id: str) -> None:
    """The transcript is written and the meeting is done being transcribed.

    Called inside the same transaction as ``persist_transcript``, so the status
    and the rows it describes commit together. Split out rather than set inline
    there because ``persist_transcript`` is about utterances, and a function
    that also moves the meeting's lifecycle along is one whose name stops
    telling you what it does.

    Not ``delivered`` and not ``awaiting_confirmation``: those are B's and E's
    to decide, later in the meeting's life. A only says that its own step
    finished.

    **The job stays ``running``.** ``done`` means the four consumers were
    told, and they are told after this commits (``mark_published``). A job
    marked done here would make a failed publish unrecoverable:
    ``mark_failed`` rightly refuses to touch a finished job.
    """
    job = session.get(TranscriptionJob, job_id)
    if job is None:
        raise NotFoundError("job", job_id)
    meeting = session.get(Meeting, job.meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", job.meeting_id)

    meeting.status = "complete"
    session.flush()


def mark_published(session: Session, *, job_id: str) -> None:
    """The event went out; this attempt is over.

    The last write of a successful run, in its own small transaction after
    ``publish``. From here a redelivery is declined at ``claim_job`` as a
    finished job; between ``mark_complete`` and this line it is declined as
    ``running``, which leaves the file to this run -- the same outcome.
    """
    job = session.get(TranscriptionJob, job_id)
    if job is None:
        raise NotFoundError("job", job_id)
    job.status = "done"
    job.finished_at = datetime.now(tz=UTC)
    session.flush()


def sweep_orphans(
    session: Session, *, settings: AudioSettings, keep: str | None = None
) -> list[str]:
    """Delete uploads nobody is coming for. Returns the job ids swept.

    The handover leaves a file for a task; a task can be lost after the
    enqueue -- broker purged, worker never came back, a route that no longer
    matches -- and then the file sits in ``temp_dir`` with no owner, which is
    the durable copy invariant 11 exists to prevent (@PARKJAEKYUNG0525 on
    #259, item 3).

    **Decided against the database, not the clock.** #209 swept on mtime and
    could delete a file a late task was about to adopt. Here a file is an
    orphan when its job says so: ``done``, ``failed`` or ``superseded`` means
    the attempt is over and the file should already be gone; no job at all
    means nothing will ever look for it. A ``queued`` or ``running`` job is
    left alone until it is older than ``orphan_after_hours``, and then both
    the file and the job are failed -- a job that old has no worker.

    A file without the ``.upload`` suffix is one ``handover`` wrote and never
    got to ``assign``: the request died between the write and the claim. There
    is no job to consult, and the write-to-assign window is one request, so
    one older than the threshold is deleted on mtime. That is the one place
    the clock decides, and it decides about a file no task can be about to
    adopt.

    Runs at the start of every ``process_recording``, skipping ``keep`` -- the
    caller's own job -- until there is a periodic trigger for it (#207, #258).
    Ids only in the log; the filenames are ids.
    """
    directory = Path(settings.temp_dir)
    if not directory.is_dir():
        return []

    cutoff = datetime.now(tz=UTC) - timedelta(hours=settings.orphan_after_hours)
    swept: list[str] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        job_id = storage.job_id_of(path)
        if job_id is None:
            if datetime.fromtimestamp(path.stat().st_mtime, tz=UTC) < cutoff:
                storage.delete_orphan(path)
                log.info("audio_orphan_unassigned_deleted")
            continue
        if job_id == keep:
            continue

        job = session.get(TranscriptionJob, job_id)
        if job is not None and job.status in ("queued", "running"):
            if job.created_at >= cutoff:
                continue
            mark_failed(session, job_id=job_id)
            log.warning("audio_job_abandoned", job_id=job_id)

        storage.delete_orphan(path)
        swept.append(job_id)
        log.info("audio_orphan_deleted", job_id=job_id, job_known=job is not None)

    return swept


def attest_consent(session: Session, *, meeting_id: str, attested_by: User) -> None:
    """Record that everyone in this meeting's recording consented, on the
    word of a member of its team.

    **The only path in the repository to ``participants.consented = True``**
    (#190). Consent is a statement about people, and before identification (#6)
    a participant row is a voice, not a person -- so there is nowhere to write
    a per-person answer, and the one honest statement available is a team
    member's about the whole meeting. Any member of the team, not necessarily
    the one who uploaded and not necessarily one who was there: ``meetings``
    has no ``created_by`` to narrow it, so the check is membership and the
    limit is documented. This records that statement and applies
    it: every participant row the meeting has *now* is set True, and
    ``persistence._participants_for`` reads the attestation for every row it
    creates *later*, so a rerun that invents a label the first run never saw
    gets the same value. Module B's condition on #190, and it is pinned by
    ``test_a_rerun_gives_a_new_label_the_same_consent``.

    Idempotent: a second call finds the row and changes nothing but the
    participant flags, which were already True. A reload is not a second
    statement.

    Not per person, not revocable, and it does not tell B and C that a meeting
    they already analysed has changed. Those are S10, S11 and #190's republish
    question. Nor does anything here undo what B, C and E derived once the
    meeting was analysed -- today the only way that data goes is with the
    meeting itself (CASCADE), and before identification (#6) there is no
    per-person unit to revoke for. The default is not loosened by any of
    this: a meeting with no attestation is exactly as it was.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=attested_by.id, team_id=meeting.team_id)

    if session.get(AudConsentAttestation, meeting_id) is None:
        session.add(AudConsentAttestation(meeting_id=meeting_id, attested_by=attested_by.id))

    result = session.execute(
        sa.update(Participant)
        .where(Participant.meeting_id == meeting_id, Participant.consented.is_(False))
        .values(consented=True)
    )
    updated = int(getattr(result, "rowcount", 0))
    session.flush()
    # Counts and ids only. Who attested is in the row; the log says it happened.
    log.info(
        "consent_attested_by_member",
        meeting_id=meeting_id,
        attested_by=attested_by.id,
        participants_updated=updated,
    )
