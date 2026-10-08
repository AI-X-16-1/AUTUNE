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

import hashlib
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, update
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

RETRY_HOLD = timedelta(minutes=9)
"""How long after a failure, or after a sweep took a meeting to retry, no
sweep takes it (again). Just under the sweep's ten minutes, so the sweep after
a failure retries it; and longer than a run, so a sweep that is still working
through its meetings when the next one starts does not have a meeting it is
in the middle of run a second time beside it (mminjae97 and lsh2217, review of
#868: after a deploy the adopted backlog can keep one sweep past ten minutes)."""

RETRY_CAP = 5
"""Meetings one sweep retries at most, oldest failure first. A backlog is
worked off over several sweeps instead of holding the worker for one."""

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


class ResultNotPublishedError(RuntimeError):
    """A run stored its result and could not tell the other modules.

    Not an extraction that failed: the action items and decisions are on the
    board. What is missing is ``ExtractionResult`` reaching D and E, so the
    retry is the publish alone -- no model is asked again -- and what the
    screen and the team's channel say is that, not "could not extract" (PARK,
    review of #868: the tab showed rows under "추출하지 못해 다시 시도 중").
    Carries the class of what the publish raised, never its message.
    """


NOT_PUBLISHED = ResultNotPublishedError.__name__
"""``reason`` for a meeting whose result is stored and was not passed on."""

NOT_EXTRACTED = "NotExtracted"
"""``reason`` for a meeting the sweep adopted: no run of it is on record at all."""

PARTLY_UNREAD = "PartlyUnread"
"""``reason`` for a meeting whose run stored its rows and could not read part
of the transcript: the model's answer for some window said nothing
(``llm.unreadable``) while others were read (the user, 2026-10-08: keep what
was read, say a part was not, try again).

Not an extraction that failed -- the rows are on the board -- and not one that
went through: what was said in the unread part is unknown. So it is counted
like a failure, which is what makes the sweep run the meeting again and the
액션 tab say so, and the tab's sentence is its own. Each further run that
still leaves a part unread counts one more, and so does one that fails
outright -- the reason stays this one until a run reads everything, because the
board still holds the partial read. After ``MAX_ATTEMPTS`` the sweep stops and
the tab keeps saying it. The team's channel is not told (``owed_notices``): its
message says a meeting could not be extracted, and this one was.

Every run asks about the whole meeting again: which windows were unread is
not kept, and a window is read with the lines before it. So a rerun follows
the rules of any rerun -- a meeting a person corrected keeps its items, a
confirmed row stays -- and what is stored is always the latest run's."""


def adopt_unextracted(session: Session, *, now: datetime | None = None) -> list[str]:
    """Count one failure for each meeting that has a transcript and no
    extraction on record -- neither a run nor a failure -- and return them.

    A run that raised before failures were counted, or a task that went with
    the worker a deploy recreated, left exactly this: lines stored by module
    A, nothing of B's, and nothing that would ever try again. Counted as the
    first failure, so the sweep makes the other two attempts."""
    when = now or datetime.now(tz=UTC)
    # The window is in WHERE, so the lines read are a week's and not the
    # table's (mminjae97, review of #868); the half hour is on the newest of
    # them.
    lost = sorted(
        session.scalars(
            select(Utterance.meeting_id)
            .outerjoin(ExtExtractionRun, ExtExtractionRun.meeting_id == Utterance.meeting_id)
            .outerjoin(
                ExtExtractionAttempt, ExtExtractionAttempt.meeting_id == Utterance.meeting_id
            )
            .where(
                Utterance.created_at > when - ADOPT_WINDOW,
                ExtExtractionRun.meeting_id.is_(None),
                ExtExtractionAttempt.meeting_id.is_(None),
            )
            .group_by(Utterance.meeting_id)
            .having(func.max(Utterance.created_at) <= when - ADOPT_AFTER)
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
    return _count(session, meeting_id, type(exc).__name__[:80], now or datetime.now(tz=UTC))


def note_partly_unread(session: Session, meeting_id: str, *, now: datetime | None = None) -> int:
    """One more run of this meeting that stored its rows with part of the
    transcript unread (``PARTLY_UNREAD``); returns how many failures there are
    now. In the run's own transaction, in the place of ``note_success``: the
    count and the rows it is about are committed together."""
    return _count(session, meeting_id, PARTLY_UNREAD, now or datetime.now(tz=UTC))


def _count(session: Session, meeting_id: str, reason: str, when: datetime) -> int:
    statement = _insert(session).values(
        meeting_id=meeting_id, failures=1, reason=reason, failed_at=when
    )
    return int(
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["meeting_id"],
                set_={
                    "failures": ExtExtractionAttempt.failures + 1,
                    # The board of a meeting counted as partly unread holds a
                    # partial read until a run goes through, whatever the run
                    # after it failed at: the reason stays, so the tab keeps
                    # saying that and not "could not extract" over rows.
                    # (``note_success`` clears it.)
                    "reason": case(
                        (ExtExtractionAttempt.reason == PARTLY_UNREAD, PARTLY_UNREAD),
                        else_=reason,
                    ),
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


def _retryable(when: datetime) -> tuple[Any, ...]:
    return (
        ExtExtractionAttempt.failures > 0,
        ExtExtractionAttempt.failures < MAX_ATTEMPTS,
        ExtExtractionAttempt.failed_at <= when - RETRY_HOLD,
    )


def due_for_retry(session: Session, *, now: datetime | None = None) -> list[str]:
    """Meetings whose extraction failed, has tries left and is not held
    (``RETRY_HOLD``): ``RETRY_CAP`` at most, oldest failure first."""
    when = now or datetime.now(tz=UTC)
    return list(
        session.scalars(
            select(ExtExtractionAttempt.meeting_id)
            .where(*_retryable(when))
            .order_by(ExtExtractionAttempt.failed_at, ExtExtractionAttempt.meeting_id)
            .limit(RETRY_CAP)
        )
    )


def claim_retry(session: Session, meeting_id: str, *, now: datetime | None = None) -> bool:
    """Whether this sweep is the one that retries the meeting now; holds it
    when so.

    One statement, asked just before the run: of two sweeps that both read the
    meeting as due, the second finds it held and passes over it. The hold is
    ``failed_at`` moved to now -- no failure is counted by it, and a run that
    then fails moves it again."""
    when = now or datetime.now(tz=UTC)
    return (
        session.execute(
            update(ExtExtractionAttempt)
            .where(ExtExtractionAttempt.meeting_id == meeting_id, *_retryable(when))
            .values(failed_at=when)
            .returning(ExtExtractionAttempt.meeting_id)
        ).first()
        is not None
    )


def failing(session: Session, meeting_ids: Collection[str]) -> set[str]:
    """Which of these meetings have a failure on record, tries left or not."""
    if not meeting_ids:
        return set()
    return set(
        session.scalars(
            select(ExtExtractionAttempt.meeting_id).where(
                ExtExtractionAttempt.meeting_id.in_(meeting_ids),
                ExtExtractionAttempt.failures > 0,
            )
        )
    )


def unpublished(session: Session, meeting_ids: Collection[str]) -> set[str]:
    """Which of these meetings failed last at passing a stored result on."""
    if not meeting_ids:
        return set()
    return set(
        session.scalars(
            select(ExtExtractionAttempt.meeting_id).where(
                ExtExtractionAttempt.meeting_id.in_(meeting_ids),
                ExtExtractionAttempt.failures > 0,
                ExtExtractionAttempt.reason == NOT_PUBLISHED,
            )
        )
    )


def owed_notices(session: Session, *, now: datetime | None = None) -> list[str]:
    """Meetings out of tries whose team has not been told yet. Not one whose
    rows are stored with a part unread (``PARTLY_UNREAD``): the message is
    about a meeting with nothing on its board."""
    when = now or datetime.now(tz=UTC)
    return list(
        session.scalars(
            select(ExtExtractionAttempt.meeting_id)
            .where(
                ExtExtractionAttempt.failures >= MAX_ATTEMPTS,
                ExtExtractionAttempt.reason.is_distinct_from(PARTLY_UNREAD),
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


NOTHING_READ = hashlib.sha256(b"").hexdigest()
"""``ExtExtractionRun.consent_key`` of a run that was allowed to read no line:
``service.consent_key`` of no ids. Written out here because ``service`` imports
this module; a test holds the two together."""


def transcribed(session: Session, meeting_id: str) -> bool:
    """Whether module A has stored any line of this meeting yet."""
    return (
        session.scalar(select(Utterance.id).where(Utterance.meeting_id == meeting_id).limit(1))
        is not None
    )


def state(session: Session, meeting_id: str, *, now: datetime | None = None) -> ExtractionState:
    """What the meeting's 액션 screen says about its extraction.

    A transcript with neither a run nor a failure on record is a first run
    that has not finished: ``in_progress`` for as long as ``adopt_unextracted``
    leaves such a meeting alone, ``overdue`` after that -- the same clock, the
    newest line module A stored, so the screen stops saying "in progress" when
    the sweep stops believing it."""
    when = now or datetime.now(tz=UTC)
    row = session.get(ExtExtractionAttempt, meeting_id)
    failures = row.failures if row is not None else 0
    run = session.execute(
        select(ExtExtractionRun.extracted_at, ExtExtractionRun.consent_key).where(
            ExtExtractionRun.meeting_id == meeting_id
        )
    ).first()
    stored = session.scalar(
        select(func.max(Utterance.created_at)).where(Utterance.meeting_id == meeting_id)
    )
    if stored is not None and stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    unrun = stored is not None and run is None and not failures
    fresh = stored is not None and stored > when - ADOPT_AFTER
    return ExtractionState(
        extracted_at=run.extracted_at if run is not None else None,
        in_progress=unrun and fresh,
        overdue=unrun and not fresh,
        read_nothing=stored is not None and run is not None and run.consent_key == NOTHING_READ,
        failures=failures,
        failed_at=row.failed_at if row is not None and failures else None,
        will_retry=0 < failures < MAX_ATTEMPTS,
        not_published=bool(failures) and row is not None and row.reason == NOT_PUBLISHED,
        partly_unread=bool(failures) and row is not None and row.reason == PARTLY_UNREAD,
        requested=row.requested if row is not None else False,
        requested_at=row.requested_at if row is not None else None,
    )
