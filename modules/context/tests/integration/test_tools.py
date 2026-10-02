"""The Meeting Context Engine's agent tools against a real PostgreSQL
(``autune_context.tools``).

Rows are seeded directly, as ``test_read_api`` does, so what is pinned is what
the Briefing subagent relies on: another team's meeting or thread reads as
missing, a meeting past its retention window is gone the moment it expires, a
quoted predecessor goes with its meeting, pending links are counted and not
listed, and ``key_stakeholders_absent`` appears nowhere in any result.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete

from autune_context import briefs, tools
from autune_context.models import (
    CtxBrief,
    CtxDecision,
    CtxDecisionVersion,
    CtxEmbedding,
    CtxMeetingStatus,
    CtxTopicLink,
)
from autune_contracts import AGENDA_STALE_AFTER, AgendaIssue, TeamAgenda
from autune_core import Meeting, Participant, Team, Utterance, session_scope
from autune_core.ids import new_id

KEYS = {"ok", "reason", "summary", "items", "evidence", "confidence", "truncated"}
ID = re.compile(r"[a-z]+_[A-Za-z0-9]+")
ABSENT = "usr_never_in_a_result"
"""A user id parked in ``key_stakeholders_absent``. It must not surface anywhere."""

NOW = datetime.now(tz=UTC)


def _team(name: str) -> str:
    with session_scope() as s:
        row = Team(name=name)
        s.add(row)
        s.flush()
        return row.id


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    tid = _team("context-tools-test")
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


@pytest.fixture
def other_team(db_engine: object) -> Iterator[str]:
    tid = _team("context-tools-other")
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


def _meeting(team_id: str, *, days_ago: int = 0, expired: bool = False) -> str:
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title="회의",
            status="analyzing",
            started_at=NOW - timedelta(days=days_ago),
            expires_at=NOW - timedelta(days=1) if expired else None,
        )
        s.add(row)
        s.flush()
        return row.id


def _linked(meeting_id: str, *, done: bool = True) -> None:
    with session_scope() as s:
        s.add(CtxMeetingStatus(meeting_id=meeting_id, topic_linking_done=done))


def _link(
    meeting_id: str,
    label: str,
    *,
    status: str = "asserted",
    confidence: float = 0.8,
    linked: str | None = None,
    linked_date: date | None = None,
) -> None:
    with session_scope() as s:
        s.add(
            CtxTopicLink(
                meeting_id=meeting_id,
                topic_label=label,
                linked_meeting_id=linked,
                linked_meeting_date=linked_date,
                similarity=0.7,
                rerank_score=0.7,
                confidence=confidence,
                status=status,
                retriever_version="test",
                reranker_version="test",
            )
        )


def _thread(team_id: str, versions: list[tuple[str, str, str, str | None, str | None]]) -> str:
    """A thread with one version per ``(meeting_id, statement, change_type,
    previous_statement, previous_meeting_id)``, oldest first."""
    with session_scope() as s:
        thread = CtxDecision(team_id=team_id, topic_label=versions[0][1])
        s.add(thread)
        s.flush()
        for meeting_id, statement, change_type, previous, previous_meeting in versions:
            s.add(
                CtxDecisionVersion(
                    thread_id=thread.id,
                    source_decision_id=new_id("dec"),
                    meeting_id=meeting_id,
                    current_statement=statement,
                    previous_statement=previous,
                    previous_meeting_id=previous_meeting,
                    change_type=change_type,
                    confidence=0.9,
                    key_stakeholders_absent=[ABSENT],
                    nli_version="test",
                )
            )
        return thread.id


def _call(fn, *args, **kwargs):
    """Run a tool on a session over the committed rows, as the registry does."""
    with session_scope() as s:
        return fn(s, *args, **kwargs)


def _holds_the_shape(result: dict) -> None:
    assert set(result) == KEYS
    assert all(ID.fullmatch(value) for value in result["evidence"])
    assert len(result["items"]) <= tools.MAX_ITEMS


# --------------------------------------------------------------------------- #
# links_for_meeting
# --------------------------------------------------------------------------- #


def test_links_list_settled_ones_and_count_the_pending(team_id: str) -> None:
    now, earlier, older = (_meeting(team_id, days_ago=n) for n in (0, 7, 14))
    _linked(now)
    _link(now, "검색 정렬", confidence=0.9, linked=earlier, linked_date=date(2026, 9, 24))
    _link(now, "배포 일정", status="confirmed", confidence=0.7, linked=older)
    _link(now, "예산 검토", status="pending", confidence=0.5, linked=older)
    _link(now, "채용 계획", status="rejected", confidence=0.5, linked=older)

    result = _call(tools.links_for_meeting, team_id, now)

    _holds_the_shape(result)
    assert result["ok"] is True
    assert [item["title"] for item in result["items"]] == ["검색 정렬", "배포 일정"]
    assert result["items"][0]["body"] == "2026-09-24 회의에서도 논의됨"
    assert result["items"][0]["linked_meeting_id"] == earlier
    assert result["evidence"] == [earlier, older]
    assert "1건" in result["summary"] and "확인을 기다리는" in result["summary"]
    assert "예산 검토" not in json.dumps(result, ensure_ascii=False)
    assert "채용 계획" not in json.dumps(result, ensure_ascii=False)


def test_links_say_so_when_the_linked_meeting_is_gone(team_id: str) -> None:
    now = _meeting(team_id)
    _linked(now)
    _link(now, "검색 정렬", linked=None, linked_date=date(2026, 9, 1))

    result = _call(tools.links_for_meeting, team_id, now)

    assert result["items"][0]["body"] == "연결된 회의는 보존 기간이 지나 삭제되었습니다."
    assert result["items"][0]["linked_meeting_id"] is None
    assert result["evidence"] == []


def test_a_meeting_with_no_links_says_none_not_an_error(team_id: str) -> None:
    now = _meeting(team_id)
    _linked(now)

    result = _call(tools.links_for_meeting, team_id, now)

    assert result["ok"] is True
    assert result["items"] == []
    assert "없습니다" in result["summary"]


def test_a_meeting_not_yet_linked_is_a_failure_the_agent_can_route_around(team_id: str) -> None:
    unlinked, unseen = _meeting(team_id), _meeting(team_id)
    _linked(unlinked, done=False)

    for meeting_id in (unlinked, unseen):
        result = _call(tools.links_for_meeting, team_id, meeting_id)
        _holds_the_shape(result)
        assert result["ok"] is False
        assert "no topic linking yet" in result["reason"]


def test_another_teams_or_an_expired_meeting_reads_as_missing(
    team_id: str, other_team: str
) -> None:
    theirs = _meeting(other_team)
    expired = _meeting(team_id, expired=True)
    for meeting_id in (theirs, expired):
        _linked(meeting_id)
        _link(meeting_id, "검색 정렬")

    for meeting_id in (theirs, expired, "mtg_unknown"):
        result = _call(tools.links_for_meeting, team_id, meeting_id)
        _holds_the_shape(result)
        assert result["ok"] is False
        assert result["reason"] == f"meeting {meeting_id} not found"
        assert result["items"] == []


# --------------------------------------------------------------------------- #
# decision_thread
# --------------------------------------------------------------------------- #


def test_a_thread_reads_oldest_first_with_the_earlier_wording(team_id: str) -> None:
    first, second = _meeting(team_id, days_ago=14), _meeting(team_id, days_ago=7)
    thread = _thread(
        team_id,
        [
            (first, "검색 정렬은 최신순으로 한다", "new", None, None),
            (
                second,
                "검색 정렬은 관련도순으로 바꾼다",
                "reversed",
                "검색 정렬은 최신순으로 한다",
                first,
            ),
        ],
    )

    result = _call(tools.decision_thread, team_id, thread)

    _holds_the_shape(result)
    assert [item["change_type"] for item in result["items"]] == ["new", "reversed"]
    assert result["items"][0]["body"] == ""
    assert result["items"][1]["body"] == "이전 결정: 검색 정렬은 최신순으로 한다"
    assert result["items"][1]["meeting_id"] == second
    assert result["items"][1]["meeting_date"] is not None
    assert "'reversed'" in result["summary"]
    assert result["evidence"][0] == thread
    assert result["truncated"] is False


def test_the_quoted_predecessor_goes_when_its_meeting_expires(team_id: str) -> None:
    first = _meeting(team_id, days_ago=14, expired=True)
    second = _meeting(team_id, days_ago=7)
    thread = _thread(
        team_id,
        [
            (first, "검색 정렬은 최신순으로 한다", "new", None, None),
            (
                second,
                "검색 정렬은 관련도순으로 바꾼다",
                "reversed",
                "검색 정렬은 최신순으로 한다",
                first,
            ),
        ],
    )

    result = _call(tools.decision_thread, team_id, thread)

    assert [item["meeting_id"] for item in result["items"]] == [second]
    assert result["items"][0]["body"] == ""
    assert "최신순" not in json.dumps(result, ensure_ascii=False)


def test_a_quote_with_no_predecessor_id_is_withheld(team_id: str) -> None:
    only = _meeting(team_id)
    thread = _thread(team_id, [(only, "검색 정렬은 관련도순", "modified", "옛 문장", None)])

    result = _call(tools.decision_thread, team_id, thread)

    assert result["items"][0]["body"] == ""
    assert "옛 문장" not in json.dumps(result, ensure_ascii=False)


def test_a_long_thread_shows_the_five_most_recent_and_says_it_was_cut(team_id: str) -> None:
    meetings = [_meeting(team_id, days_ago=30 - n) for n in range(7)]
    thread = _thread(
        team_id,
        [(m, f"결정 {n}", "modified" if n else "new", None, None) for n, m in enumerate(meetings)],
    )

    result = _call(tools.decision_thread, team_id, thread)

    assert [item["title"] for item in result["items"]] == [f"결정 {n}" for n in range(2, 7)]
    assert result["truncated"] is True
    assert "7개 회의" in result["summary"]


def test_another_teams_and_a_fully_expired_thread_read_as_missing(
    team_id: str, other_team: str
) -> None:
    theirs = _thread(other_team, [(_meeting(other_team), "남의 결정", "new", None, None)])
    gone = _thread(team_id, [(_meeting(team_id, expired=True), "만료된 결정", "new", None, None)])

    for thread in (theirs, gone, "thr_unknown"):
        result = _call(tools.decision_thread, team_id, thread)
        _holds_the_shape(result)
        assert result["ok"] is False
        assert result["reason"] == f"decision thread {thread} not found"
        assert "결정" not in json.dumps(result["items"], ensure_ascii=False)


# --------------------------------------------------------------------------- #
# list_decisions
# --------------------------------------------------------------------------- #


def test_list_decisions_filters_by_topic_and_change_type(team_id: str) -> None:
    a, b = _meeting(team_id, days_ago=3), _meeting(team_id, days_ago=2)
    _thread(team_id, [(a, "검색 정렬은 최신순으로 한다", "new", None, None)])
    _thread(team_id, [(b, "배포는 금요일에 한다", "reversed", None, None)])

    everything = _call(tools.list_decisions, team_id)
    by_topic = _call(tools.list_decisions, team_id, topic="검색")
    by_change = _call(tools.list_decisions, team_id, change_type="reversed")

    for result in (everything, by_topic, by_change):
        _holds_the_shape(result)
    assert len(everything["items"]) == 2
    assert [item["title"] for item in by_topic["items"]] == ["검색 정렬은 최신순으로 한다"]
    assert [item["title"] for item in by_change["items"]] == ["배포는 금요일에 한다"]
    assert by_topic["evidence"] == [by_topic["items"][0]["id"]]


def test_list_decisions_is_scoped_to_the_team(team_id: str, other_team: str) -> None:
    _thread(other_team, [(_meeting(other_team), "남의 결정", "new", None, None)])

    result = _call(tools.list_decisions, team_id)

    assert result["ok"] is True
    assert result["items"] == []
    assert "남의 결정" not in json.dumps(result, ensure_ascii=False)


def test_list_decisions_caps_at_five_and_says_so(team_id: str) -> None:
    for n in range(7):
        _thread(team_id, [(_meeting(team_id, days_ago=n), f"결정 {n}", "new", None, None)])

    result = _call(tools.list_decisions, team_id)

    assert len(result["items"]) == tools.MAX_ITEMS
    assert result["truncated"] is True
    assert "7건" in result["summary"]


def test_an_unknown_change_type_is_refused_not_searched(team_id: str) -> None:
    result = _call(tools.list_decisions, team_id, change_type="drifted")

    _holds_the_shape(result)
    assert result["ok"] is False


# --------------------------------------------------------------------------- #
# what no tool may return
# --------------------------------------------------------------------------- #


def test_no_result_carries_who_was_absent(team_id: str) -> None:
    now, first = _meeting(team_id), _meeting(team_id, days_ago=7)
    _linked(now)
    _link(now, "검색 정렬", linked=first)
    thread = _thread(
        team_id,
        [
            (first, "검색 정렬은 최신순으로 한다", "new", None, None),
            (
                now,
                "검색 정렬은 관련도순으로 바꾼다",
                "reversed",
                "검색 정렬은 최신순으로 한다",
                first,
            ),
        ],
    )

    results = [
        _call(tools.links_for_meeting, team_id, now),
        _call(tools.decision_thread, team_id, thread),
        _call(tools.list_decisions, team_id),
    ]

    dumped = json.dumps(results, ensure_ascii=False)
    assert ABSENT not in dumped
    assert "absent" not in dumped.lower()
    assert "stakeholder" not in dumped.lower()


# --------------------------------------------------------------------------- #
# brief_recap and brief_agenda
# --------------------------------------------------------------------------- #


def _scheduled(team_id: str, *, expired: bool = False) -> str:
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title="주간 회의",
            status="scheduled",
            started_at=NOW + timedelta(minutes=10),
            expires_at=NOW - timedelta(days=1) if expired else NOW + timedelta(days=90),
        )
        s.add(row)
        s.flush()
        return row.id


def _past(team_id: str, *, topics: tuple[str, ...] = (), decisions: int = 0) -> str:
    """An analysed earlier meeting, its topics cut from consenting speech."""
    with session_scope() as s:
        meeting = Meeting(
            team_id=team_id,
            title="지난 주간 회의",
            status="complete",
            started_at=NOW - timedelta(days=7),
            expires_at=NOW + timedelta(days=90),
        )
        s.add(meeting)
        s.flush()
        s.add(CtxMeetingStatus(meeting_id=meeting.id, topic_linking_done=True))
        speaker = Participant(meeting_id=meeting.id, speaker_label="화자", consented=True)
        s.add(speaker)
        s.flush()
        for i, label in enumerate(topics):
            utterance = Utterance(
                meeting_id=meeting.id,
                participant_id=speaker.id,
                speaker_label="화자",
                start_sec=float(i),
                end_sec=float(i) + 1,
                text=f"{label} 이야기",
            )
            s.add(utterance)
            s.flush()
            s.add(
                CtxEmbedding(
                    meeting_id=meeting.id,
                    kind="topic",
                    ref_label=label,
                    embedding=[0.0] * 1024,
                    model_version="test",
                    utterance_ids=[utterance.id],
                )
            )
        for n in range(decisions):
            thread = CtxDecision(team_id=team_id, topic_label=f"결정 {n}")
            s.add(thread)
            s.flush()
            s.add(
                CtxDecisionVersion(
                    thread_id=thread.id,
                    source_decision_id=new_id("dec"),
                    meeting_id=meeting.id,
                    current_statement=f"결정 {n}",
                    change_type="reversed" if n == 0 else "new",
                    confidence=0.9,
                    nli_version="test",
                )
            )
        return meeting.id


def _composed(meeting_id: str, previous: str | None, reason: str | None) -> None:
    with session_scope() as s:
        s.add(CtxBrief(meeting_id=meeting_id, previous_meeting_id=previous, match_reason=reason))


def _agenda(team_id: str, *titles: str, age: timedelta = timedelta()) -> None:
    agenda = TeamAgenda(
        team_id=team_id,
        as_of=datetime.now(tz=UTC) - age,
        issues=[
            AgendaIssue(
                title=title,
                key=f"AUT-{i}",
                status="진행 중",
                url=f"https://example.atlassian.net/browse/AUT-{i}",
            )
            for i, title in enumerate(titles, start=1)
        ],
    )
    with session_scope() as s:
        briefs.store_team_agenda(s, agenda)


def test_the_recap_names_the_meeting_it_follows_then_its_decisions(team_id: str) -> None:
    past = _past(team_id, topics=("검색 정렬", "배포 일정"), decisions=2)
    upcoming = _scheduled(team_id)
    _composed(upcoming, past, briefs.SERIES)

    result = _call(tools.brief_recap, team_id, upcoming)

    _holds_the_shape(result)
    assert result["ok"] is True
    first, *decisions = result["items"]
    assert first["kind"] == "previous_meeting"
    assert first["meeting_id"] == past
    assert first["title"] == "지난 주간 회의"
    assert "같은 제목의 지난 회의" in first["body"]
    assert "주제: 검색 정렬, 배포 일정" in first["body"]
    assert [d["kind"] for d in decisions] == ["decision", "decision"]
    assert decisions[0]["title"].endswith("(번복)")
    assert result["evidence"] == [past]
    assert result["truncated"] is False


def test_a_long_recap_keeps_the_meeting_and_four_decisions_and_says_it_was_cut(
    team_id: str,
) -> None:
    past = _past(team_id, decisions=6)
    upcoming = _scheduled(team_id)
    _composed(upcoming, past, briefs.LATEST)

    result = _call(tools.brief_recap, team_id, upcoming)

    assert [i["kind"] for i in result["items"]] == ["previous_meeting"] + ["decision"] * 4
    assert result["truncated"] is True
    assert "6건" in result["summary"]


def test_a_brief_not_composed_yet_is_a_failure_the_agent_can_route_around(team_id: str) -> None:
    upcoming = _scheduled(team_id)

    result = _call(tools.brief_recap, team_id, upcoming)

    _holds_the_shape(result)
    assert result["ok"] is False
    assert result["reason"] == tools.BRIEF_NOT_COMPOSED


def test_a_recap_whose_meeting_was_deleted_says_so(team_id: str) -> None:
    upcoming = _scheduled(team_id)
    _composed(upcoming, None, briefs.SERIES)  # SET NULL by the deletion, reason kept

    result = _call(tools.brief_recap, team_id, upcoming)

    assert result["ok"] is True
    assert result["items"] == []
    assert "삭제" in result["summary"]


def test_a_team_with_no_earlier_analysed_meeting_gets_an_empty_recap(team_id: str) -> None:
    upcoming = _scheduled(team_id)
    _composed(upcoming, None, None)

    result = _call(tools.brief_recap, team_id, upcoming)

    assert result["ok"] is True
    assert result["items"] == []
    assert "이전에 분석된 회의가 없습니다" in result["summary"]


def test_a_recap_goes_when_the_earlier_meeting_expires(team_id: str) -> None:
    past = _past(team_id, decisions=1)
    upcoming = _scheduled(team_id)
    _composed(upcoming, past, briefs.SERIES)
    with session_scope() as s:
        meeting = s.get(Meeting, past)
        assert meeting is not None
        meeting.expires_at = NOW - timedelta(minutes=1)

    result = _call(tools.brief_recap, team_id, upcoming)

    assert result["items"] == []
    assert "삭제" in result["summary"]
    assert past not in json.dumps(result)


def test_the_agenda_lists_the_issues_with_key_status_and_link(team_id: str) -> None:
    _agenda(team_id, "결제 모듈 API 명세 정리", "온보딩 화면 시안 공유")
    upcoming = _scheduled(team_id)

    result = _call(tools.brief_agenda, team_id, upcoming)

    _holds_the_shape(result)
    assert [i["title"] for i in result["items"]] == [
        "결제 모듈 API 명세 정리",
        "온보딩 화면 시안 공유",
    ]
    assert result["items"][0]["body"] == "AUT-1 · 진행 중"
    assert result["items"][0]["url"] == "https://example.atlassian.net/browse/AUT-1"
    assert result["evidence"] == []


def test_the_agenda_works_before_the_brief_is_composed(team_id: str) -> None:
    """The issues come from B's snapshot, not from the choice D makes at T-10."""
    _agenda(team_id, "미리 보는 이슈")

    result = _call(tools.brief_agenda, team_id, _scheduled(team_id))

    assert result["ok"] is True
    assert len(result["items"]) == 1


def test_no_issue_and_a_snapshot_b_stopped_refreshing_both_read_as_none(team_id: str) -> None:
    upcoming = _scheduled(team_id)
    none_at_all = _call(tools.brief_agenda, team_id, upcoming)
    _agenda(team_id, "오래된 이슈", age=AGENDA_STALE_AFTER + timedelta(minutes=1))
    stale = _call(tools.brief_agenda, team_id, upcoming)

    for result in (none_at_all, stale):
        assert result["ok"] is True
        assert result["items"] == []
        assert "없습니다" in result["summary"]


def test_a_long_agenda_shows_five_and_says_it_was_cut(team_id: str) -> None:
    _agenda(team_id, *(f"이슈 {n}" for n in range(8)))

    result = _call(tools.brief_agenda, team_id, _scheduled(team_id))

    assert len(result["items"]) == 5
    assert result["truncated"] is True
    assert "8건" in result["summary"]


def test_another_teams_or_an_expired_meeting_reads_as_missing_for_both_brief_tools(
    team_id: str, other_team: str
) -> None:
    theirs = _scheduled(other_team)
    expired = _scheduled(team_id, expired=True)
    _composed(theirs, None, None)
    _composed(expired, None, None)

    for meeting in (theirs, expired, "mtg_doesnotexist"):
        for tool in (tools.brief_recap, tools.brief_agenda):
            result = _call(tool, team_id, meeting)
            assert result["ok"] is False, (tool.__name__, meeting)
            assert result["items"] == []


def test_every_tool_is_registered_with_a_docstring_to_route_on() -> None:
    assert [fn.__name__ for fn in tools.TOOLS] == [
        "links_for_meeting",
        "decision_thread",
        "list_decisions",
        "brief_recap",
        "brief_agenda",
    ]
    for fn in tools.TOOLS:
        assert inspect.getdoc(fn), fn.__name__
        assert "speakingratio" not in fn.__name__.replace("_", "")
    assert tools.PERSONAL_ONLY_TOOLS == []
    assert tools.RUN_SCOPE == ("team_id",)
