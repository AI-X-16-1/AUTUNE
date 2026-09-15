"""Business logic for module A: Audio Pipeline.

Owner: 김민경. See docs/modules/audio.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``aud_*`` tables —
except ``meetings``, ``participants`` and ``utterances``, which invariant 4
assigns to this module and nobody else.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts.enums import TranscriptSource
from autune_core import Meeting, Team, TeamMember, User, get_logger, session_scope
from autune_core.errors import NotFoundError, PermissionDeniedError

log = get_logger(__name__)


def create_meeting(
    session: Session,
    *,
    uploader: User,
    team_id: str,
    title: str,
    source: TranscriptSource = TranscriptSource.FILE_UPLOAD,
) -> Meeting:
    """The ``meetings`` row a recording is about to be attached to.

    **Membership is checked here, not assumed from the token.** A valid token
    proves who is asking, not which teams they may write a meeting into; without
    this check anyone signed in could attach a recording — and therefore a
    transcript, and therefore four modules' analysis — to a team they have
    nothing to do with.

    ``status`` starts at ``analyzing`` rather than ``scheduled``: the task is
    queued in the same request, so the meeting is never in a state where nothing
    is going to happen to it. Once ``process_recording`` records its outcome, a
    meeting left in ``analyzing`` will be how a lost task shows up; nothing moves
    the status off ``analyzing`` yet, so today it means only "queued at least
    once" and a finished meeting reads the same as a lost one.

    **``started_at`` stays ``None``, and that is not an omission.** The only time
    this route knows is when the file was uploaded, which is not when the meeting
    happened. Module B resolves every relative deadline against this column
    (``slots.meeting_day``) and its docstring names this exact case: a Friday
    meeting uploaded on Monday moves every "내일" by three days. It answers
    ``None`` with no date at all and keeps the phrase for a person to read, which
    is the outcome to want — a missing deadline gets filled in, a confident wrong
    one never gets looked at again. D reads the column through
    ``started_at or now()`` and a ``coalesce`` onto ``created_at``; E does not
    read it. If a real meeting time is ever needed here it has to come from
    whoever uploads, and no upload screen collects one today (S03, S10).

    **``expires_at`` is set from the team's retention window.** Nothing else in
    the repository writes this column, and module D's ``visible_meeting_clauses``
    reads ``expires_at IS NULL`` as "never expires" — so a meeting created
    without it is one the retention sweep and every retention-aware read will
    ignore forever (#206). The window is resolved at creation because that is
    the promise made to the people in the room at the time; a team that later
    shortens its retention does not retroactively un-record what was agreed,
    and one that lengthens it does not reach back into meetings whose speakers
    were told 90 days.
    """
    team = session.get(Team, team_id)
    if team is None:
        raise NotFoundError("team", team_id)

    member = session.scalar(
        sa.select(TeamMember.id).where(
            TeamMember.team_id == team_id, TeamMember.user_id == uploader.id
        )
    )
    if member is None:
        raise PermissionDeniedError("you are not a member of this team")

    now = datetime.now(tz=UTC)
    meeting = Meeting(
        team_id=team_id,
        title=title,
        started_at=None,
        status="analyzing",
        source=source.value,
        expires_at=now + timedelta(days=team.retention_days),
    )
    session.add(meeting)
    session.flush()

    # Ids only. A meeting title is something a person typed about a meeting.
    log.info("audio_meeting_created", meeting_id=meeting.id, team_id=team_id, source=source.value)
    return meeting


def abandon_meeting(meeting_id: str) -> None:
    """Mark a meeting ``failed`` when its task never reached the queue.

    Its own session, because the caller is inside an ``except`` block and the
    session that created the meeting has already committed — that is exactly the
    situation this exists for. ``upload_recording`` commits the row and *then*
    queues, so a broker that refuses leaves a meeting nothing will ever process:
    no file (the route deletes it), no task, and `analyzing` forever. The sweep
    covers the file; nothing covered the row.

    **Never raises.** It runs while another exception is on its way out, and the
    original is the one that says why the request failed. A cleanup that masks it
    would trade a broker error for a database one and lose the reason.
    """
    try:
        with session_scope() as session:
            meeting = session.get(Meeting, meeting_id)
            if meeting is not None:
                meeting.status = "failed"
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.error("audio_abandon_failed", meeting_id=meeting_id, error=type(exc).__name__)
        return
    log.warning("audio_meeting_abandoned", meeting_id=meeting_id)
