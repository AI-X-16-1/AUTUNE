"""A meeting that is deleted takes its decision text out of D (#1161).

Driven the way every caller deletes a meeting -- module A's retention sweep,
its team deletion and a member's own deletion: ``run_meeting_hooks`` with the
real registry, then the ``meetings`` row in a transaction of its own, and the
cascade. What is asserted is what PostgreSQL holds afterwards.

Seeds the rows directly, as ``test_speech_deleted.py`` does: what is under
test is which rows the hook changes, not how lineage built them. The hook
opens its own session, so these commit real rows and clean up by deleting the
teams.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, select
from structlog.testing import capture_logs

from autune_context import service
from autune_context.constants import EMBEDDING_DIM
from autune_context.models import (
    CtxBrief,
    CtxDecision,
    CtxDecisionVersion,
    CtxEmbedding,
    CtxMeetingStatus,
    CtxTopicLink,
)
from autune_core import Base, Meeting, Team, get_settings, session_scope
from autune_core.deletion import registered_modules, run_meeting_hooks

MARK = "지울회의표식"
"""In every statement and label the meeting under deletion wrote, and in
nothing any other meeting wrote."""

_VECTOR = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
_LONG = "남는 회의의 긴 결정 " + "가" * 500


@pytest.fixture
def teams(db_engine: object) -> Iterator[tuple[str, str]]:  # db_engine ensures migrations ran
    with session_scope() as s:
        mine = Team(name="context-meeting-deleted-test")
        other = Team(name="context-meeting-deleted-other")
        s.add_all([mine, other])
        s.flush()
        ids = (mine.id, other.id)
    yield ids
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id.in_(ids)))


def _meeting(s, team_id: str, days_ago: int, *, expired: bool = False) -> str:  # noqa: ANN001
    now = datetime.now(tz=UTC)
    row = Meeting(
        team_id=team_id,
        title="회의",
        status="complete",
        started_at=now - timedelta(days=days_ago),
        expires_at=now - timedelta(days=1) if expired else now + timedelta(days=30),
    )
    s.add(row)
    s.flush()
    return row.id


def _thread(s, team_id: str, label: str) -> str:  # noqa: ANN001
    row = CtxDecision(team_id=team_id, topic_label=label[:400])
    s.add(row)
    s.flush()
    return row.id


def _version(  # noqa: ANN001
    s,
    thread_id: str,
    meeting_id: str,
    statement: str,
    previous: CtxDecisionVersion | None = None,
) -> CtxDecisionVersion:
    row = CtxDecisionVersion(
        thread_id=thread_id,
        source_decision_id=f"dec_{statement[:20]}",
        source_utterance_ids=[],
        meeting_id=meeting_id,
        current_statement=statement,
        previous_version_id=previous.id if previous else None,
        previous_statement=previous.current_statement if previous else None,
        previous_meeting_id=previous.meeting_id if previous else None,
        change_type="reversed" if previous else "new",
        nli_label="contradiction" if previous else None,
        confidence=0.77 if previous else 0.9,
        key_stakeholders_absent=[],
        nli_version="fake",
    )
    s.add(row)
    s.flush()
    return row


@dataclass(frozen=True)
class Seeded:
    earlier: str
    meeting: str  # the one deleted
    later: str
    expired: str
    elsewhere: str  # a meeting of the other team
    through: str  # earlier -> meeting -> later
    through_first: int
    through_follower: int
    headed: str  # earlier -> meeting, meeting twice: the meeting holds the head
    headed_first: int
    headed_mine: int
    headed_mine_again: int
    only_here: str  # the meeting alone, twice
    with_expired: str  # an expired meeting -> meeting
    with_expired_first: int
    apart: str  # earlier -> later: the meeting is not in it
    apart_follower: int
    theirs: str  # the other team's thread
    theirs_version: int


def seed(teams: tuple[str, str]) -> Seeded:
    """Five threads of one team around the meeting under deletion, one that
    never met it, and one of another team."""
    team_id, other_team = teams
    with session_scope() as s:
        earlier = _meeting(s, team_id, 30)
        meeting = _meeting(s, team_id, 20)
        later = _meeting(s, team_id, 10)
        expired = _meeting(s, team_id, 200, expired=True)
        elsewhere = _meeting(s, other_team, 20)

        through = _thread(s, team_id, "배포는 수요일로 다시 옮긴다")
        t1 = _version(s, through, earlier, "배포는 금요일에 한다")
        t2 = _version(s, through, meeting, f"{MARK} 배포는 월요일로 미룬다", t1)
        t3 = _version(s, through, later, "배포는 수요일로 다시 옮긴다", t2)

        headed = _thread(s, team_id, f"{MARK} 가격은 다시 올린다")
        h1 = _version(s, headed, earlier, _LONG)
        h2 = _version(s, headed, meeting, f"{MARK} 가격은 내린다", h1)
        h3 = _version(s, headed, meeting, f"{MARK} 가격은 다시 올린다", h2)

        only_here = _thread(s, team_id, f"{MARK} 채용은 두 명으로 한다")
        o1 = _version(s, only_here, meeting, f"{MARK} 채용은 한 명으로 한다")
        _version(s, only_here, meeting, f"{MARK} 채용은 두 명으로 한다", o1)

        with_expired = _thread(s, team_id, f"{MARK} 사무실은 옮기지 않는다")
        e1 = _version(s, with_expired, expired, "사무실을 옮긴다")
        _version(s, with_expired, meeting, f"{MARK} 사무실은 옮기지 않는다", e1)

        apart = _thread(s, team_id, "회식 날짜")
        a1 = _version(s, apart, earlier, "회식은 화요일에 한다")
        a2 = _version(s, apart, later, "회식은 목요일에 한다", a1)

        theirs = _thread(s, other_team, "다른 팀이 붙인 이름")
        x1 = _version(s, theirs, elsewhere, "다른 팀의 결정")
        x2 = _version(s, theirs, elsewhere, "다른 팀의 결정을 바꾼다", x1)

        # What the cascade takes, and the two pointers it empties.
        s.add_all(
            [
                CtxEmbedding(
                    meeting_id=meeting,
                    kind="topic",
                    ref_label=f"{MARK} 주제",
                    utterance_ids=[],
                    embedding=_VECTOR,
                    model_version="fake",
                ),
                _link(meeting, f"{MARK} 주제", earlier),
                _link(later, "다음 회의의 주제", meeting),
                CtxMeetingStatus(meeting_id=meeting),
                CtxBrief(meeting_id=later, previous_meeting_id=meeting, match_reason="series"),
            ]
        )
        s.flush()
        return Seeded(
            earlier=earlier,
            meeting=meeting,
            later=later,
            expired=expired,
            elsewhere=elsewhere,
            through=through,
            through_first=t1.id,
            through_follower=t3.id,
            headed=headed,
            headed_first=h1.id,
            headed_mine=h2.id,
            headed_mine_again=h3.id,
            only_here=only_here,
            with_expired=with_expired,
            with_expired_first=e1.id,
            apart=apart,
            apart_follower=a2.id,
            theirs=theirs,
            theirs_version=x2.id,
        )


def _link(meeting_id: str, label: str, past: str) -> CtxTopicLink:
    return CtxTopicLink(
        meeting_id=meeting_id,
        topic_label=label,
        linked_meeting_id=past,
        linked_meeting_date=datetime.now(tz=UTC).date(),
        similarity=0.9,
        rerank_score=0.9,
        confidence=0.9,
        retriever_version="fake",
        reranker_version="fake",
    )


def delete_as_a_caller_does(meeting_id: str) -> None:
    """Hooks first, each in its own transaction; then the row, and the cascade."""
    run_meeting_hooks(meeting_id)
    with session_scope() as s:
        s.execute(delete(Meeting).where(Meeting.id == meeting_id))


def version(version_id: int) -> CtxDecisionVersion | None:
    with session_scope() as s:
        row = s.get(CtxDecisionVersion, version_id)
        if row is not None:
            s.expunge(row)
        return row


def label(thread_id: str) -> str | None:
    with session_scope() as s:
        return s.scalar(select(CtxDecision.topic_label).where(CtxDecision.id == thread_id))


def versions_of(thread_id: str) -> list[int]:
    with session_scope() as s:
        return list(
            s.scalars(
                select(CtxDecisionVersion.id)
                .where(CtxDecisionVersion.thread_id == thread_id)
                .order_by(CtxDecisionVersion.id)
            )
        )


def rows_holding(text: str) -> dict[str, int]:
    """Every ``ctx_`` table with a row whose any column reads ``text``, counted.

    Reads each row as PostgreSQL prints it, so a column added later is covered
    without being named here."""
    found: dict[str, int] = {}
    with session_scope() as s:
        for table in Base.metadata.sorted_tables:
            if not table.name.startswith("ctx_"):
                continue
            count = s.scalar(
                sa.text(f"SELECT count(*) FROM {table.name} AS t WHERE t::text LIKE :needle"),  # noqa: S608
                {"needle": f"%{text}%"},
            )
            if count:
                found[table.name] = count
    return found


def snapshot() -> list[tuple]:
    """Every decision thread and version, as stored -- to tell "nothing changed"."""
    with session_scope() as s:
        threads = s.execute(
            select(CtxDecision.id, CtxDecision.topic_label).order_by(CtxDecision.id)
        ).all()
        versions = s.execute(
            select(
                CtxDecisionVersion.id,
                CtxDecisionVersion.thread_id,
                CtxDecisionVersion.current_statement,
                CtxDecisionVersion.previous_statement,
                CtxDecisionVersion.previous_meeting_id,
                CtxDecisionVersion.previous_version_id,
                CtxDecisionVersion.change_type,
            ).order_by(CtxDecisionVersion.id)
        ).all()
    return [tuple(row) for row in threads] + [tuple(row) for row in versions]


# --------------------------------------------------------------------------- #
# What is left once the meeting has gone
# --------------------------------------------------------------------------- #


def test_nothing_the_meeting_wrote_is_left_in_any_ctx_table(teams: tuple[str, str]) -> None:
    seeded = seed(teams)
    assert set(rows_holding(MARK)) == {
        "ctx_decisions",
        "ctx_decision_versions",
        "ctx_embeddings",
        "ctx_topic_links",
    }

    delete_as_a_caller_does(seeded.meeting)

    assert rows_holding(MARK) == {}


def test_the_cascade_alone_leaves_the_text_in_other_rows(teams: tuple[str, str]) -> None:
    """What the hook is for: without it the statement and the labels stay."""
    seeded = seed(teams)

    with session_scope() as s:
        s.execute(delete(Meeting).where(Meeting.id == seeded.meeting))

    # The follower's copy, and the three threads the meeting headed or was
    # alone in.
    assert rows_holding(MARK) == {"ctx_decisions": 3, "ctx_decision_versions": 1}


def test_a_later_versions_copy_is_emptied_and_what_changed_stays(teams: tuple[str, str]) -> None:
    seeded = seed(teams)

    delete_as_a_caller_does(seeded.meeting)

    follower = version(seeded.through_follower)
    assert follower is not None
    assert follower.previous_statement is None
    assert follower.current_statement == "배포는 수요일로 다시 옮긴다"
    # That it changed, how, and after which meeting.
    assert follower.previous_meeting_id == seeded.meeting
    assert (follower.change_type, follower.nli_label, follower.confidence) == (
        "reversed",
        "contradiction",
        0.77,
    )
    # The pointer to the version that went is the cascade's to empty.
    assert follower.previous_version_id is None
    # The thread keeps its other two versions, and its label was never the
    # meeting's.
    assert versions_of(seeded.through) == [seeded.through_first, seeded.through_follower]
    assert label(seeded.through) == "배포는 수요일로 다시 옮긴다"


def test_a_thread_the_meeting_headed_is_named_after_the_version_before(
    teams: tuple[str, str],
) -> None:
    seeded = seed(teams)

    delete_as_a_caller_does(seeded.meeting)

    assert label(seeded.headed) == _LONG[:400]
    assert versions_of(seeded.headed) == [seeded.headed_first]
    first = version(seeded.headed_first)
    assert first is not None and first.current_statement == _LONG


def test_a_thread_only_the_meeting_is_in_is_deleted(teams: tuple[str, str]) -> None:
    seeded = seed(teams)

    delete_as_a_caller_does(seeded.meeting)

    assert label(seeded.only_here) is None
    assert versions_of(seeded.only_here) == []


def test_a_thread_left_with_only_an_expired_meeting_has_no_label(teams: tuple[str, str]) -> None:
    """An expired meeting is no head (``sweep_stale_topic_labels``): its
    statement is not what the thread is named after, and the thread is not
    empty either -- the version is there until that meeting's own sweep."""
    seeded = seed(teams)

    delete_as_a_caller_does(seeded.meeting)

    assert label(seeded.with_expired) == ""
    assert versions_of(seeded.with_expired) == [seeded.with_expired_first]


def test_a_thread_the_meeting_is_not_in_and_another_teams_are_left_alone(
    teams: tuple[str, str],
) -> None:
    """Their labels are not what their heads read, on purpose: a label is the
    lineage build's to set, and this hook renames a thread only for the
    meeting that is going."""
    seeded = seed(teams)

    delete_as_a_caller_does(seeded.meeting)

    assert label(seeded.apart) == "회식 날짜"
    apart = version(seeded.apart_follower)
    assert apart is not None
    assert apart.previous_statement == "회식은 화요일에 한다"
    assert apart.previous_meeting_id == seeded.earlier
    assert label(seeded.theirs) == "다른 팀이 붙인 이름"
    theirs = version(seeded.theirs_version)
    assert theirs is not None
    assert theirs.previous_statement == "다른 팀의 결정"
    assert theirs.previous_meeting_id == seeded.elsewhere


def test_the_cascade_takes_the_meetings_own_rows_and_empties_the_pointers_to_it(
    teams: tuple[str, str],
) -> None:
    seeded = seed(teams)

    delete_as_a_caller_does(seeded.meeting)

    with session_scope() as s:
        for model in (CtxEmbedding, CtxTopicLink, CtxDecisionVersion):
            assert s.scalars(select(model).where(model.meeting_id == seeded.meeting)).all() == []
        assert s.get(CtxMeetingStatus, seeded.meeting) is None
        link = s.scalars(select(CtxTopicLink).where(CtxTopicLink.meeting_id == seeded.later)).one()
        assert link.linked_meeting_id is None
        assert link.topic_label == "다음 회의의 주제"
        brief = s.get(CtxBrief, seeded.later)
        assert brief is not None and brief.previous_meeting_id is None


# --------------------------------------------------------------------------- #
# The hook on its own: before the row goes, twice, and when nothing is there
# --------------------------------------------------------------------------- #


def test_the_hook_is_done_before_the_row_goes(teams: tuple[str, str]) -> None:
    """The caller deletes in a transaction the hook is not part of, and may
    fail to. What the hook changed is then already changed."""
    seeded = seed(teams)

    run_meeting_hooks(seeded.meeting)

    with session_scope() as s:
        assert s.get(Meeting, seeded.meeting) is not None
    follower = version(seeded.through_follower)
    assert follower is not None and follower.previous_statement is None
    assert label(seeded.headed) == _LONG[:400]
    assert label(seeded.only_here) is None
    assert label(seeded.with_expired) == ""


def test_the_meetings_own_versions_in_a_thread_that_stays_are_left_whole(
    teams: tuple[str, str],
) -> None:
    """They go with the row. Until it does -- and if it does not -- they read
    as they did, the one that follows another of the same meeting included."""
    seeded = seed(teams)

    run_meeting_hooks(seeded.meeting)

    mine = version(seeded.headed_mine)
    again = version(seeded.headed_mine_again)
    assert mine is not None and again is not None
    assert mine.current_statement == f"{MARK} 가격은 내린다"
    assert mine.previous_statement == _LONG
    assert again.previous_meeting_id == seeded.meeting
    assert again.previous_statement == f"{MARK} 가격은 내린다"


def test_running_the_hook_again_changes_nothing_more(teams: tuple[str, str]) -> None:
    seeded = seed(teams)
    run_meeting_hooks(seeded.meeting)
    once = snapshot()

    with session_scope() as s:
        again = service.forget_meeting(s, seeded.meeting)

    assert again == service.MeetingForgotten()
    assert snapshot() == once


def test_running_it_after_the_row_went_changes_nothing(teams: tuple[str, str]) -> None:
    seeded = seed(teams)
    delete_as_a_caller_does(seeded.meeting)
    gone = snapshot()

    run_meeting_hooks(seeded.meeting)

    assert snapshot() == gone


def test_a_meeting_d_holds_nothing_of_changes_nothing(teams: tuple[str, str]) -> None:
    seeded = seed(teams)
    with session_scope() as s:
        bare = _meeting(s, teams[0], 5)
    before = snapshot()

    with session_scope() as s:
        done = service.forget_meeting(s, bare)

    assert done == service.MeetingForgotten()
    assert snapshot() == before
    # Nor does an id nobody holds.
    with session_scope() as s:
        assert service.forget_meeting(s, "mtg_nobody") == service.MeetingForgotten()
    assert snapshot() == before
    assert seeded.meeting != bare


def test_the_counts_say_what_was_done(teams: tuple[str, str]) -> None:
    seeded = seed(teams)

    with session_scope() as s:
        done = service.forget_meeting(s, seeded.meeting)

    # One follower's copy; `headed` and `with_expired` renamed (`through` was
    # never named after the meeting); `only_here` deleted.
    assert done == service.MeetingForgotten(
        statements_cleared=1, labels_refreshed=2, threads_deleted=1
    )


def test_the_log_line_carries_ids_and_counts_and_no_statement(teams: tuple[str, str]) -> None:
    seeded = seed(teams)

    with capture_logs() as logs:
        run_meeting_hooks(seeded.meeting)

    mine = [entry for entry in logs if entry["event"] == "context_meeting_forgotten"]
    assert mine == [
        {
            "event": "context_meeting_forgotten",
            "log_level": "info",
            "meeting_id": seeded.meeting,
            "statements_cleared": 1,
            "labels_refreshed": 2,
            "threads_deleted": 1,
        }
    ]
    assert MARK not in str(logs)
    assert "배포" not in str(logs)


# --------------------------------------------------------------------------- #
# As a hook: registered, raising, and under the caller's lock
# --------------------------------------------------------------------------- #


def test_a_failure_reaches_the_caller_and_nothing_is_half_done(
    teams: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller keeps the meeting when a hook raises. One transaction: a
    failure after the copies were emptied leaves them as they were."""
    seeded = seed(teams)
    before = snapshot()

    def fails(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("no")

    # Read for the labels, after the copies were emptied and the lone thread
    # was deleted.
    monkeypatch.setattr(service, "visible_meeting_clauses", fails)
    with pytest.raises(RuntimeError):
        run_meeting_hooks(seeded.meeting)
    monkeypatch.undo()

    assert snapshot() == before


def test_the_hook_does_not_wait_for_the_callers_lock(teams: tuple[str, str]) -> None:
    """A team's deletion holds ``FOR NO KEY UPDATE`` on every meeting of the
    team, and a member's on the one meeting, while the hooks run in sessions
    of their own. A hook that needed one of those rows -- the meeting's, or
    another's it reads for a head -- would wait for its own caller."""
    seeded = seed(teams)
    engine = sa.create_engine(get_settings().database_url)
    try:
        with engine.connect() as caller:
            caller.execute(
                select(Meeting.id)
                .where(Meeting.team_id == teams[0])
                .with_for_update(key_share=True)
            )
            caller.execute(
                select(Team.id).where(Team.id == teams[0]).with_for_update(key_share=True)
            )
            failed: list[BaseException] = []

            def run() -> None:
                try:
                    run_meeting_hooks(seeded.meeting)
                except BaseException as exc:  # noqa: BLE001 -- reported below
                    failed.append(exc)

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            worker.join(timeout=20)
            waiting = worker.is_alive()
            caller.rollback()
            worker.join(timeout=20)
    finally:
        engine.dispose()

    assert not waiting
    assert failed == []
    assert label(seeded.only_here) is None


def test_the_hook_is_registered_in_this_process() -> None:
    assert "context" in registered_modules()[0]


@pytest.mark.parametrize("entry", ["autune_context.router", "autune_context.tasks"])
def test_the_hook_is_registered_where_the_api_and_the_worker_start(entry: str) -> None:
    """In a fresh interpreter. The API process, where a member or a team's
    last member deletes, imports routers and never ``tasks``; the worker,
    where a meeting expires, imports ``tasks``. This file has already imported
    ``service``, so an in-process check would pass without either."""
    probe = (
        f"import {entry}\n"
        "from autune_core.deletion import registered_modules\n"
        "assert 'context' in registered_modules()[0]\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
