"""What became of a meeting's extraction since it last went through.

An extraction that raised used to leave nothing behind: no row, no retry, and
a meeting whose 액션 tab stayed empty with nothing to say why (dev,
2026-10-05). ``ext_extraction_attempts`` keeps the count of failures in a row,
so that the sweep can try again, the team can be told once the tries are spent,
and the meeting's own screen can show it -- and a person's request to run the
extraction again, which waits here for the worker: the API process has no
broker to queue on (``tasks.sync_after_confirmation`` says why).

Counts, times and the class of the last error. Never the error's message: an
exception raised over a meeting's rows can carry what was said in it
(privacy.md section 6).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from autune_core import Utterance

from .models import ExtExtractionAttempt, ExtExtractionRun
from .schemas import ExtractionState

MAX_ATTEMPTS = 3
"""Failures in a row after which the sweep stops trying and the team is told
(the user, 2026-10-06: the first attempt counts as one of the three)."""

NOTICE_WINDOW = timedelta(days=1)
"""How long after the last failure the team's channel is still owed its notice.
A Slack that did not answer is asked again on the next sweep; a team that
connects a channel a week later is not told about a week-old failure."""

REQUEST_COOLDOWN = timedelta(minutes=2)
"""How long after one "다시 추출" the next is refused. A run is a minute or
two of model calls, so a press sooner than this would start a second run
beside the first."""


def _insert(session: Session) -> Any:
    dialect = session.get_bind().dialect.name
    return (postgresql.insert if dialect == "postgresql" else sqlite.insert)(ExtExtractionAttempt)


ADOPT_AFTER = timedelta(minutes=30)
"""How long a stored transcript may go without an extraction before the sweep
takes it for one that was lost. The event's own run starts as soon as the
transcript is published and takes minutes."""

ADOPT_WINDOW = timedelta(days=7)
"""How far back ``adopt_unextracted`` looks. An older meeting nobody extracted
is not one somebody is still waiting for."""

NOT_EXTRACTED = "NotExtracted"
"""``reason`` for a meeting the sweep adopted: no run of it is on record at all."""


def adopt_unextracted(session: Session, *, now: datetime | None = None) -> list[str]:
    """Count one failure for each meeting that has a transcript and no
    extraction on record -- neither a run nor a failure -- and return them.

    A run that raised before failures were counted, or a task that went with
    the worker a deploy recreated, left exactly this: lines stored by module
    A, nothing of B's, and nothing that would ever try again. Counted as the
    first failure, so the sweep makes the other two attempts."""
    when = now or datetime.now(tz=UTC)
    newest = func.max(Utterance.created_at)
    lost = sorted(
        session.scalars(
            select(Utterance.meeting_id)
            .outerjoin(ExtExtractionRun, ExtExtractionRun.meeting_id == Utterance.meeting_id)
            .outerjoin(
                ExtExtractionAttempt, ExtExtractionAttempt.meeting_id == Utterance.meeting_id
            )
            .where(
                ExtExtractionRun.meeting_id.is_(None),
                ExtExtractionAttempt.meeting_id.is_(None),
            )
            .group_by(Utterance.meeting_id)
            .having(newest <= when - ADOPT_AFTER, newest > when - ADOPT_WINDOW)
        )
    )
    for meeting_id in lost:
        session.execute(
            _insert(session)
            .values(meeting_id=meeting_id, failures=1, reason=NOT_EXTRACTED, failed_at=when)
            .on_conflict_do_nothing(index_elements=["meeting_id"])
        )
    return lost


def note_failure(
    session: Session, meeting_id: str, exc: BaseException, *, now: datetime | None = None
) -> int:
    """One more failure for this meeting; returns how many there are now.

    One statement, so the event's task and the sweep failing at once count
    two and not one."""
    when = now or datetime.now(tz=UTC)
    reason = type(exc).__name__[:80]
    statement = _insert(session).values(
        meeting_id=meeting_id, failures=1, reason=reason, failed_at=when
    )
    return int(
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["meeting_id"],
                set_={
                    "failures": ExtExtractionAttempt.failures + 1,
                    "reason": reason,
                    "failed_at": when,
                },
            ).returning(ExtExtractionAttempt.failures)
        ).scalar_one()
    )


def note_success(session: Session, meeting_id: str) -> None:
    """The meeting's extraction went through: nothing is owed for it any more.
    Called in the extraction's own transaction, so a rollback keeps the count.
    The row stays for ``requested_at``."""
    session.execute(
        update(ExtExtractionAttempt)
        .where(ExtExtractionAttempt.meeting_id == meeting_id)
        .values(failures=0, reason=None, failed_at=None, told_at=None)
    )


def due_for_retry(session: Session) -> list[str]:
    """Meetings whose extraction failed and has tries left, oldest failure first."""
    return list(
        session.scalars(
            select(ExtExtractionAttempt.meeting_id)
            .where(
                ExtExtractionAttempt.failures > 0,
                ExtExtractionAttempt.failures < MAX_ATTEMPTS,
            )
            .order_by(ExtExtractionAttempt.failed_at, ExtExtractionAttempt.meeting_id)
        )
    )


def owed_notices(session: Session, *, now: datetime | None = None) -> list[str]:
    """Meetings out of tries whose team has not been told yet."""
    when = now or datetime.now(tz=UTC)
    return list(
        session.scalars(
            select(ExtExtractionAttempt.meeting_id)
            .where(
                ExtExtractionAttempt.failures >= MAX_ATTEMPTS,
                ExtExtractionAttempt.told_at.is_(None),
                ExtExtractionAttempt.failed_at > when - NOTICE_WINDOW,
            )
            .order_by(ExtExtractionAttempt.failed_at, ExtExtractionAttempt.meeting_id)
        )
    )


def claim_request(session: Session, meeting_id: str, *, now: datetime | None = None) -> bool:
    """Whether a person's "다시 추출" may start a run now; records it when so.

    One statement, as ``sync_state.claim_retry``: two presses at once cannot
    both pass."""
    when = now or datetime.now(tz=UTC)
    statement = _insert(session).values(
        meeting_id=meeting_id, failures=0, requested=True, requested_at=when
    )
    claimed = session.execute(
        statement.on_conflict_do_update(
            index_elements=["meeting_id"],
            set_={"requested": True, "requested_at": statement.excluded.requested_at},
            where=ExtExtractionAttempt.requested_at.is_(None)
            | (ExtExtractionAttempt.requested_at <= when - REQUEST_COOLDOWN),
        ).returning(ExtExtractionAttempt.meeting_id)
    ).first()
    return claimed is not None


def take_requests(session: Session) -> list[str]:
    """The meetings a person asked to extract again, each handed out once:
    the flag is cleared by the statement that reads it."""
    return sorted(
        session.scalars(
            update(ExtExtractionAttempt)
            .where(ExtExtractionAttempt.requested.is_(True))
            .values(requested=False)
            .returning(ExtExtractionAttempt.meeting_id)
        )
    )


def claim_notice(session: Session, meeting_id: str, *, now: datetime | None = None) -> bool:
    """Whether this sweep is the one that tells the team; marks it told when so."""
    return (
        session.execute(
            update(ExtExtractionAttempt)
            .where(
                ExtExtractionAttempt.meeting_id == meeting_id,
                ExtExtractionAttempt.told_at.is_(None),
            )
            .values(told_at=now or datetime.now(tz=UTC))
            .returning(ExtExtractionAttempt.meeting_id)
        ).first()
        is not None
    )


def release_notice(session: Session, meeting_id: str) -> None:
    """The notice did not go: owed again, for the next sweep."""
    session.execute(
        update(ExtExtractionAttempt)
        .where(ExtExtractionAttempt.meeting_id == meeting_id)
        .values(told_at=None)
    )


def transcribed(session: Session, meeting_id: str) -> bool:
    """Whether module A has stored any line of this meeting yet."""
    return (
        session.scalar(select(Utterance.id).where(Utterance.meeting_id == meeting_id).limit(1))
        is not None
    )


def state(session: Session, meeting_id: str) -> ExtractionState:
    """What the meeting's 액션 screen says about its extraction."""
    row = session.get(ExtExtractionAttempt, meeting_id)
    failures = row.failures if row is not None else 0
    return ExtractionState(
        extracted_at=session.scalar(
            select(ExtExtractionRun.extracted_at).where(ExtExtractionRun.meeting_id == meeting_id)
        ),
        failures=failures,
        failed_at=row.failed_at if row is not None and failures else None,
        will_retry=0 < failures < MAX_ATTEMPTS,
        requested=row.requested if row is not None else False,
        requested_at=row.requested_at if row is not None else None,
    )
