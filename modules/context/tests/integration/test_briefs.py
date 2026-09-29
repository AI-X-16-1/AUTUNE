"""Pre-meeting briefs against a real PostgreSQL: which meetings are due, which
past meeting a brief recaps, the claim, and what the recap reads back.

Rows are built directly, as ``test_notify.py`` does -- these are about the
brief, not about how topic linking or lineage built the rows it reads. The
models are the ``fake`` implementations: the re-ranker is token overlap, so a
title that shares its words with a past meeting's topics is a confident match.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from autune_context import briefs
from autune_context.config import get_settings
from autune_context.models import (
    CtxBrief,
    CtxDecision,
    CtxDecisionVersion,
    CtxEmbedding,
    CtxMeetingStatus,
)
from autune_context.pipeline import get_embedder, reset_cache
from autune_contracts import ChangeType
from autune_core import Meeting, Team, session_scope
from autune_core.errors import NotFoundError

NOW = datetime(2026, 9, 30, 5, 50, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fake_models(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for knob in ("EMBEDDER", "RERANKER", "NLI"):
        monkeypatch.setenv(f"AUTUNE_CONTEXT_{knob}_IMPL", "fake")
    get_settings.cache_clear()
    reset_cache()
    yield
    get_settings.cache_clear()
    reset_cache()


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        row = Team(name="brief-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


def _scheduled(
    team_id: str,
    *,
    title: str = "주간 회의",
    starts_in: timedelta = timedelta(minutes=10),
    status: str = "scheduled",
) -> str:
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title=title,
            status=status,
            started_at=NOW + starts_in,
            expires_at=NOW + timedelta(days=90),
        )
        s.add(row)
        s.flush()
        return row.id


def _analyzed(
    team_id: str,
    *,
    title: str,
    days_ago: int,
    topics: tuple[str, ...] = (),
    decisions: tuple[tuple[str, ChangeType], ...] = (),
    expires_at: datetime | None = None,
) -> str:
    """A past meeting D has processed: its status row, topics and decisions."""
    embedder = get_embedder()
    with session_scope() as s:
        meeting = Meeting(
            team_id=team_id,
            title=title,
            status="complete",
            started_at=NOW - timedelta(days=days_ago),
            expires_at=expires_at or NOW + timedelta(days=90),
        )
        s.add(meeting)
        s.flush()
        s.add(CtxMeetingStatus(meeting_id=meeting.id, topic_linking_done=True))
        for label, vector in zip(topics, embedder.embed(list(topics)), strict=True):
            s.add(
                CtxEmbedding(
                    meeting_id=meeting.id,
                    kind="topic",
                    ref_label=label,
                    embedding=vector,
                    model_version=embedder.model_version,
                )
            )
        for statement, change_type in decisions:
            thread = CtxDecision(team_id=team_id, topic_label=statement)
            s.add(thread)
            s.flush()
            s.add(
                CtxDecisionVersion(
                    thread_id=thread.id,
                    source_decision_id="dec_test",
                    meeting_id=meeting.id,
                    current_statement=statement,
                    change_type=change_type.value,
                    confidence=0.9,
                    nli_version="test",
                )
            )
        return meeting.id


def _compose(meeting_id: str, *, will_send: bool = True) -> briefs.Brief | None:
    with session_scope() as s:
        return briefs.compose_due_brief(s, meeting_id, now=NOW, will_send=will_send)


# --------------------------------------------------------------------------- #
# Which meetings are due
# --------------------------------------------------------------------------- #


def test_due_is_a_scheduled_meeting_inside_the_lead_time(team_id: str) -> None:
    due = _scheduled(team_id, starts_in=timedelta(minutes=10))
    soon = _scheduled(team_id, starts_in=timedelta(minutes=2))
    later = _scheduled(team_id, starts_in=timedelta(minutes=11))
    started = _scheduled(team_id, starts_in=timedelta(minutes=-1))
    recording = _scheduled(team_id, status="recording")

    with session_scope() as s:
        ids = set(briefs.due_meeting_ids(s, NOW))

    assert {due, soon} <= ids
    assert not {later, started, recording} & ids


def test_a_meeting_with_a_brief_is_no_longer_due(team_id: str) -> None:
    meeting = _scheduled(team_id)
    _compose(meeting)

    with session_scope() as s:
        assert meeting not in briefs.due_meeting_ids(s, NOW)


# --------------------------------------------------------------------------- #
# Which past meeting it recaps
# --------------------------------------------------------------------------- #


def test_a_recurring_meeting_recaps_its_last_occurrence(team_id: str) -> None:
    last_week = _analyzed(team_id, title="주간  회의", days_ago=7)
    _analyzed(team_id, title="결제 모듈 점검", days_ago=1)  # newer, but another series
    meeting = _scheduled(team_id, title="주간 회의")

    brief = _compose(meeting)

    assert brief is not None
    assert brief.recap is not None and brief.recap.meeting_id == last_week
    assert brief.match_reason == briefs.SERIES


def test_a_new_title_recaps_the_meeting_that_discussed_its_topic(team_id: str) -> None:
    about_search = _analyzed(team_id, title="스프린트 3", days_ago=5, topics=("검색 정렬 개선",))
    _analyzed(team_id, title="스프린트 4", days_ago=1, topics=("결제 모듈 출시",))
    meeting = _scheduled(team_id, title="검색 정렬 개선")

    brief = _compose(meeting)

    assert brief is not None
    assert brief.recap is not None and brief.recap.meeting_id == about_search
    assert brief.match_reason == briefs.TOPIC


def test_with_no_confident_match_the_latest_meeting_is_recapped(team_id: str) -> None:
    _analyzed(team_id, title="스프린트 3", days_ago=5, topics=("검색 정렬",))
    latest = _analyzed(team_id, title="스프린트 4", days_ago=1, topics=("결제 모듈",))
    meeting = _scheduled(team_id, title="분기 회고")

    brief = _compose(meeting)

    assert brief is not None
    assert brief.recap is not None and brief.recap.meeting_id == latest
    assert brief.match_reason == briefs.LATEST


def test_a_model_failure_falls_back_to_the_latest_meeting(
    team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _analyzed(team_id, title="스프린트 3", days_ago=5, topics=("검색 정렬 개선",))
    latest = _analyzed(team_id, title="스프린트 4", days_ago=1)
    meeting = _scheduled(team_id, title="검색 정렬 개선")

    def _down(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("embedder unreachable")

    monkeypatch.setattr(briefs, "_topic_match", _down)
    brief = _compose(meeting)

    assert brief is not None
    assert brief.recap is not None and brief.recap.meeting_id == latest


def test_expired_and_unanalyzed_meetings_are_never_recapped(team_id: str) -> None:
    _analyzed(team_id, title="주간 회의", days_ago=3, expires_at=NOW - timedelta(minutes=1))
    with session_scope() as s:  # never processed by D: no status row
        s.add(Meeting(team_id=team_id, title="주간 회의", status="failed", started_at=NOW))
    meeting = _scheduled(team_id)

    brief = _compose(meeting)

    assert brief is not None
    assert brief.recap is None and brief.match_reason is None
    assert brief.recap_gone is False


# --------------------------------------------------------------------------- #
# The claim, and what reads back
# --------------------------------------------------------------------------- #


def test_the_recap_carries_topics_and_decisions_with_their_change(team_id: str) -> None:
    _analyzed(
        team_id,
        title="주간 회의",
        days_ago=7,
        topics=("검색 정렬", "결제 모듈", "검색 정렬"),
        decisions=(("결제 모듈 출시는 10월로 미룬다", ChangeType.REVERSED),),
    )
    meeting = _scheduled(team_id)

    brief = _compose(meeting)

    assert brief is not None and brief.recap is not None
    assert brief.recap.topics == ("검색 정렬", "결제 모듈")
    assert [(d.statement, d.change_type) for d in brief.recap.decisions] == [
        ("결제 모듈 출시는 10월로 미룬다", ChangeType.REVERSED)
    ]


def test_a_brief_is_claimed_once(team_id: str) -> None:
    meeting = _scheduled(team_id)

    first = _compose(meeting)
    second = _compose(meeting)

    assert first is not None and first.sent_at == NOW
    assert second is None


def test_a_team_without_slack_gets_a_brief_that_is_not_marked_sent(team_id: str) -> None:
    meeting = _scheduled(team_id)

    brief = _compose(meeting, will_send=False)

    assert brief is not None and brief.sent_at is None
    with session_scope() as s:
        row = s.get(CtxBrief, meeting)
        assert row is not None and row.sent_at is None


def test_a_meeting_that_already_started_gets_no_brief(team_id: str) -> None:
    meeting = _scheduled(team_id, status="recording")

    assert _compose(meeting) is None
    with session_scope() as s:
        assert s.get(CtxBrief, meeting) is None


def test_the_recap_is_read_live_and_shows_a_deleted_meeting_as_gone(team_id: str) -> None:
    previous = _analyzed(team_id, title="주간 회의", days_ago=7, topics=("검색 정렬",))
    meeting = _scheduled(team_id)
    _compose(meeting)

    with session_scope() as s:
        s.execute(delete(Meeting).where(Meeting.id == previous))

    with session_scope() as s:
        brief = briefs.get_brief(s, meeting, now=NOW)
        row = s.get(CtxBrief, meeting)
        assert row is not None and row.previous_meeting_id is None  # SET NULL
    assert brief.recap is None
    assert brief.recap_gone is True


def test_an_expired_past_meeting_is_gone_before_the_sweep_runs(team_id: str) -> None:
    _analyzed(team_id, title="주간 회의", days_ago=7, expires_at=NOW + timedelta(hours=1))
    meeting = _scheduled(team_id)
    _compose(meeting)

    with session_scope() as s:
        brief = briefs.get_brief(s, meeting, now=NOW + timedelta(hours=2))

    assert brief.recap is None
    assert brief.recap_gone is True


def test_no_brief_is_readable_before_it_is_composed(team_id: str) -> None:
    meeting = _scheduled(team_id)

    with session_scope() as s, pytest.raises(NotFoundError):
        briefs.get_brief(s, meeting, now=NOW)


def test_deleting_the_scheduled_meeting_deletes_its_brief(team_id: str) -> None:
    meeting = _scheduled(team_id)
    _compose(meeting)

    with session_scope() as s:
        s.execute(delete(Meeting).where(Meeting.id == meeting))

    with session_scope() as s:
        assert s.get(CtxBrief, meeting) is None
