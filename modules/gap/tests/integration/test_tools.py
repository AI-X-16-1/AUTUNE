"""C's agent tools against a real PostgreSQL (``autune_gap.tools``).

Seeds topic graphs directly and lets ``service.detect_gaps`` store the gaps, as
``test_gap_detection`` does, then reads them through the tools. What these pin
is what the Follow-up subagent relies on: undismissed gaps only, the team's
previous analysed meeting, another team's meeting reads as missing, and no
figure about a person in either result.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from autune_core import Meeting, Participant, Team, session_scope
from autune_gap import service, tools
from autune_gap.models import GapGap, GapParticipation, GapTopic

COVERS_TWO = {"핵심 지표": 1.0, "담당자": 0.9}
"""Matches ``general``'s ``success_criteria`` and ``ownership`` only, so the
meeting is left open on ``risk``, ``dependency`` and ``next_step``."""

COVERS_RISK_TOO = {**COVERS_TWO, "리스크": 0.8}
"""The same, with ``risk`` settled."""

T0 = datetime(2026, 9, 1, 10, tzinfo=UTC)

KEYS = {"ok", "reason", "summary", "items", "evidence", "confidence", "truncated"}
ID = re.compile(r"[a-z]+_[A-Za-z0-9]+")
PERSONAL = ("participant", "participation", "silent", "speaker", "spoke", "person")


def _team(name: str) -> str:
    with session_scope() as s:
        row = Team(name=name)
        s.add(row)
        s.flush()
        return row.id


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    tid = _team("gap-tools-test")
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


@pytest.fixture
def other_team(db_engine: object) -> Iterator[str]:
    tid = _team("gap-tools-other")
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


def meeting(team_id: str, topics: dict[str, float], *, started: datetime | None) -> str:
    """A meeting with a built topic graph, detected. Returns its id."""
    with session_scope() as s:
        row = Meeting(team_id=team_id, title="회의", status="analyzing", started_at=started)
        s.add(row)
        s.flush()
        person = Participant(meeting_id=row.id, speaker_label="화자0", consented=True)
        s.add(person)
        s.flush()
        for label, centrality in topics.items():
            topic = GapTopic(
                meeting_id=row.id,
                label=label,
                extractor_version="fake",
                centrality=centrality,
                betweenness=0.0,
            )
            s.add(topic)
            s.flush()
            s.add(GapParticipation(topic_id=topic.id, participant_id=person.id, spoke=True))
        meeting_id = row.id
    service.detect_gaps(meeting_id)
    return meeting_id


def dismiss(meeting_id: str, key: str) -> None:
    with session_scope() as s:
        gap = s.scalar(
            select(GapGap).where(GapGap.meeting_id == meeting_id, GapGap.template_item_key == key)
        )
        assert gap is not None
        gap.dismissed_at = datetime.now(UTC)


def call(fn, team_id: str, meeting_id: str) -> dict:  # type: ignore[no-untyped-def]
    with session_scope() as s:
        result = fn(s, team_id=team_id, meeting_id=meeting_id)
    assert set(result) == KEYS
    assert all(ID.fullmatch(e) for e in result["evidence"])
    assert len(result["items"]) <= tools.MAX_ITEMS
    return result


def keys_of(result: dict) -> set[str]:
    return {item["template_item_key"] for item in result["items"]}


def test_open_gaps_lists_what_the_meeting_left_open(team_id: str) -> None:
    m = meeting(team_id, COVERS_TWO, started=T0)

    result = call(tools.open_gaps, team_id, m)

    assert result["ok"]
    assert keys_of(result) == {"risk", "dependency", "next_step"}
    scores = [item["score"] for item in result["items"]]
    assert scores == sorted(scores, reverse=True)
    assert set(result["evidence"]) == {item["id"] for item in result["items"]}


def test_a_dismissed_gap_is_not_open(team_id: str) -> None:
    m = meeting(team_id, COVERS_TWO, started=T0)
    dismiss(m, "risk")

    assert "risk" not in keys_of(call(tools.open_gaps, team_id, m))


def test_recurring_finds_what_the_previous_meeting_also_left_open(team_id: str) -> None:
    first = meeting(team_id, COVERS_TWO, started=T0)
    second = meeting(team_id, COVERS_RISK_TOO, started=T0 + timedelta(days=7))

    result = call(tools.recurring_open_gaps, team_id, second)

    assert result["ok"]
    # risk was settled the second time, so only the other two carried over.
    assert keys_of(result) == {"dependency", "next_step"}
    assert {item["previous_meeting_id"] for item in result["items"]} == {first}
    assert len(result["evidence"]) == 2 * len(result["items"])


def test_the_same_key_under_another_template_is_not_recurring(team_id: str) -> None:
    first = meeting(team_id, COVERS_TWO, started=T0)
    with session_scope() as s:
        for gap in s.scalars(select(GapGap).where(GapGap.meeting_id == first)):
            gap.template_key = "feature_planning"
    second = meeting(team_id, COVERS_TWO, started=T0 + timedelta(days=7))

    result = call(tools.recurring_open_gaps, team_id, second)

    assert result["ok"]
    assert result["items"] == []


def test_recurring_reads_the_latest_earlier_meeting_only(team_id: str) -> None:
    meeting(team_id, COVERS_TWO, started=T0)
    middle = meeting(team_id, COVERS_TWO, started=T0 + timedelta(days=7))
    for key in ("risk", "dependency", "next_step"):
        dismiss(middle, key)
    last = meeting(team_id, COVERS_TWO, started=T0 + timedelta(days=14))

    # Nothing was open in the meeting right before, whatever the first one left.
    assert call(tools.recurring_open_gaps, team_id, last)["items"] == []


def test_recurring_skips_a_meeting_c_never_analysed(team_id: str) -> None:
    first = meeting(team_id, COVERS_TWO, started=T0)
    with session_scope() as s:
        s.add(Meeting(team_id=team_id, title="분석 전", started_at=T0 + timedelta(days=3)))
    second = meeting(team_id, COVERS_TWO, started=T0 + timedelta(days=7))

    result = call(tools.recurring_open_gaps, team_id, second)

    assert {item["previous_meeting_id"] for item in result["items"]} == {first}


def test_a_first_meeting_has_nothing_recurring(team_id: str) -> None:
    only = meeting(team_id, COVERS_TWO, started=T0)

    result = call(tools.recurring_open_gaps, team_id, only)

    assert result["ok"]
    assert result["items"] == []


def test_another_teams_meeting_reads_as_missing(team_id: str, other_team: str) -> None:
    theirs = meeting(other_team, COVERS_TWO, started=T0)

    for fn in (tools.open_gaps, tools.recurring_open_gaps):
        result = call(fn, team_id, theirs)
        assert not result["ok"]
        assert result["items"] == []


def test_another_teams_meeting_is_never_the_previous_one(team_id: str, other_team: str) -> None:
    meeting(other_team, COVERS_TWO, started=T0)
    mine = meeting(team_id, COVERS_TWO, started=T0 + timedelta(days=7))

    assert call(tools.recurring_open_gaps, team_id, mine)["items"] == []


def test_a_meeting_with_no_graph_is_not_analysed_yet(team_id: str) -> None:
    with session_scope() as s:
        row = Meeting(team_id=team_id, title="분석 전", started_at=T0)
        s.add(row)
        s.flush()
        pending = row.id

    for fn in (tools.open_gaps, tools.recurring_open_gaps):
        assert not call(fn, team_id, pending)["ok"]


def test_no_result_says_anything_about_a_person(team_id: str) -> None:
    meeting(team_id, COVERS_TWO, started=T0)
    second = meeting(team_id, COVERS_TWO, started=T0 + timedelta(days=7))

    for fn in (tools.open_gaps, tools.recurring_open_gaps):
        for item in call(fn, team_id, second)["items"]:
            assert not [k for k in item if any(word in k for word in PERSONAL)]
