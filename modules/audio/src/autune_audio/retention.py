"""The retention window, enforced: expired meetings and idle voice profiles go.

privacy.md section 4 says analysis results are kept for the team's retention
window (90 days by default) and that "a scheduled sweep deletes expired
results". Until this module nothing did (#206): ``create_meeting`` wrote
``meetings.expires_at`` and module D hid what had passed it, but no row was
ever deleted, so the window was a display rule rather than a retention one.

**A deletes the meeting because A is the only writer of ``meetings``**
(invariant 4). Everything derived from it in every module is reachable by
``ON DELETE CASCADE`` from ``meetings.id`` -- privacy.md section 4 requires
that of every module table -- so one ``DELETE`` here takes B's, C's, D's and
E's rows with it. Anything a module keeps outside PostgreSQL is what
``autune_core.deletion.on_meeting_deleted`` is for, and those hooks run first.

**A voice profile is bounded by its owner's meetings** (#363, item 1). A
profile is biometric data and was the one thing exempt from the window: it has
to outlive any single meeting or identification cannot offer you in the next
one. It does not have to outlive all of them. Once every meeting a person was
identified in has expired, nothing in the product still connects them to a
meeting, and a voice vector kept "in case they come back" is the unbounded
retention #363 objects to. So a profile goes in the same sweep that takes the
person's last meeting -- a person who keeps attending keeps their profile, and
one who stopped is forgotten one retention window after they stopped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Participant, Team, get_logger
from autune_core.deletion import run_meeting_hooks

from .models import AudSpeakerEmbedding

log = get_logger(__name__)


@dataclass(frozen=True)
class SweepResult:
    """What one sweep removed. Counts and ids only -- a title can name a client."""

    backfilled: int
    meetings: tuple[str, ...]
    hooks_failed: tuple[str, ...]
    profiles: int


def backfill_expiry(session: Session) -> int:
    """Give every meeting created before #206 the window it should have had.

    ``create_meeting`` has set ``expires_at`` since the upload endpoint
    landed, but rows written before that -- and by tests and seed scripts that
    build a ``Meeting`` directly -- carry ``NULL``, which module D and this
    sweep both read as "never expires". Filled from when the meeting was held
    (``started_at``, else ``created_at``) and its team's ``retention_days`` --
    the anchor ``create_meeting`` uses -- so a backfilled row ends up where it
    would have been had it been created today's way.
    """
    anchor = sa.func.coalesce(Meeting.started_at, Meeting.created_at)
    result = session.execute(
        sa.update(Meeting)
        .where(Meeting.expires_at.is_(None), Meeting.team_id == Team.id)
        .values(expires_at=anchor + sa.func.make_interval(0, 0, 0, Team.retention_days))
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0))


def expired_meeting_ids(session: Session, *, now: datetime, limit: int) -> list[str]:
    """Up to ``limit`` meetings whose window has closed, oldest first.

    **A scheduled meeting that has not happened yet is never expired.** Its
    ``expires_at`` is provisional until it is held -- ``service.open_retention_window``
    restarts the window when the first live hello or the upload arrives -- and
    until then it has no transcript and nothing derived from it, so sparing it
    keeps nothing the window is meant to bound.

    ``LIMIT`` in SQL, not a slice of every expired id: the first run on a
    database that never swept reads the whole backlog otherwise (review of #581).
    """
    not_yet_held = sa.and_(Meeting.status == "scheduled", Meeting.started_at > now)
    return list(
        session.scalars(
            sa.select(Meeting.id)
            .where(Meeting.expires_at <= now, sa.not_(not_yet_held))
            .order_by(Meeting.expires_at)
            .limit(limit)
        )
    )


def forget_idle_profiles(session: Session) -> int:
    """Delete the voice profile of anyone no remaining meeting names.

    A profile row (``user_id`` set) exists only because ``assign_speaker``
    named its owner on a participant row, so "no participant row points at
    this user" means every meeting that ever identified them has gone -- by
    this sweep, by a deletion, or by a later correction naming someone else.
    Observation rows (``meeting_id`` set) are not touched: they cascade with
    their meeting already.
    """
    still_named = (
        sa.select(Participant.id).where(Participant.user_id == AudSpeakerEmbedding.user_id).exists()
    )
    result = session.execute(
        sa.delete(AudSpeakerEmbedding).where(
            AudSpeakerEmbedding.user_id.is_not(None), sa.not_(still_named)
        )
    )
    return int(getattr(result, "rowcount", 0))


def sweep(session: Session, *, now: datetime, batch: int = 200) -> SweepResult:
    """Backfill, delete what expired, then drop the profiles that lost their last meeting.

    **Each meeting's hooks run before its row goes, and a failing hook keeps
    the row.** A hook cleans up what the cascade cannot reach; deleting the
    meeting after its hook failed would leave that thing with nothing left to
    find it by. The meeting stays expired, D already hides it, and the next run
    tries again. Hooks take only an id and own their sessions, so they cannot
    join this transaction -- which is why the order is hook, then ``DELETE``.

    **So a hook can run twice for one meeting**: when its ``DELETE`` or this
    transaction's commit fails after it succeeded, and when two runs overlap.
    Every ``on_meeting_deleted`` hook must be safe to repeat
    (``autune_core.deletion`` says the same).

    ``batch`` bounds one run, so a backlog (the first run after #206, on a
    database that never swept) is worked through over several runs rather
    than in one transaction holding a lock on every expired meeting.
    """
    backfilled = backfill_expiry(session)

    deleted: list[str] = []
    failed: list[str] = []
    for meeting_id in expired_meeting_ids(session, now=now, limit=batch):
        try:
            run_meeting_hooks(meeting_id)
        except Exception as exc:  # noqa: BLE001 -- any hook failure keeps the row
            # The type only: a hook's message is another module's text and
            # may quote what it was cleaning up.
            log.warning("retention_hook_failed", meeting_id=meeting_id, error=type(exc).__name__)
            failed.append(meeting_id)
            continue
        session.execute(sa.delete(Meeting).where(Meeting.id == meeting_id))
        deleted.append(meeting_id)

    profiles = forget_idle_profiles(session)
    session.flush()
    return SweepResult(
        backfilled=backfilled,
        meetings=tuple(deleted),
        hooks_failed=tuple(failed),
        profiles=profiles,
    )
