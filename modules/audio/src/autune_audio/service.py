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
from autune_core import Meeting, Team, TeamMember, User, get_logger
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
    is going to happen to it. A meeting left in ``analyzing`` is how a lost task
    shows up, which is the point of not calling it ``scheduled``.

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
        started_at=now,
        status="analyzing",
        source=source.value,
        expires_at=now + timedelta(days=team.retention_days),
    )
    session.add(meeting)
    session.flush()

    # Ids only. A meeting title is something a person typed about a meeting.
    log.info("audio_meeting_created", meeting_id=meeting.id, team_id=team_id, source=source.value)
    return meeting
