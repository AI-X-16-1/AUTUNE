"""Recomputing stored questions with C's own logic (``service.refresh_questions``).

The backfill for gaps raised before a missing item's question named the
meeting's subject. What these pin: an old generic question is replaced by what
``detect.question_for`` gives today; a freshly detected meeting needs nothing;
coverage, score and severity are never touched; and the report, E's payload and
the agent's ``gap.open_gaps`` read the same question afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select

from autune_core import Meeting, Participant, Team, session_scope
from autune_gap import refresh_questions as cli
from autune_gap import service, tools
from autune_gap.models import GapGap, GapParticipation, GapTopic
from autune_gap.template import get_template

TOPICS = {"핵심 지표": 1.0, "담당자": 0.9, "리스크": 0.2}
"""``success_criteria`` and ``ownership`` covered, ``risk`` partial on a thin
topic, ``dependency`` and ``next_step`` missing. 핵심 지표 is the subject."""


def _team(name: str) -> str:
    with session_scope() as s:
        row = Team(name=name)
        s.add(row)
        s.flush()
        return row.id


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    tid = _team("gap-refresh-test")
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


def seed(team_id: str) -> str:
    with session_scope() as s:
        meeting = Meeting(team_id=team_id, title="회의", status="analyzing")
        s.add(meeting)
        s.flush()
        person = Participant(meeting_id=meeting.id, speaker_label="화자0", consented=True)
        s.add(person)
        s.flush()
        for label, centrality in TOPICS.items():
            topic = GapTopic(
                meeting_id=meeting.id,
                label=label,
                extractor_version="fake",
                centrality=centrality,
                betweenness=0.0,
            )
            s.add(topic)
            s.flush()
            s.add(GapParticipation(topic_id=topic.id, participant_id=person.id, spoke=True))
        meeting_id = meeting.id
    service.detect_gaps(meeting_id)
    return meeting_id


def rows(meeting_id: str) -> dict[str, dict[str, Any]]:
    """Each stored gap's verdict and question, by template item key."""
    with session_scope() as s:
        found = s.scalars(select(GapGap).where(GapGap.meeting_id == meeting_id))
        return {
            str(g.template_item_key): {
                "id": g.id,
                "question": g.suggested_question,
                "verdict": (g.title, g.coverage, g.risk_score, g.severity, g.dismissed_at),
            }
            for g in found
        }


def make_generic(meeting_id: str, *keys: str) -> None:
    """What a gap raised before this change holds: the template's own question."""
    general = {item.key: item for item in get_template("general").items}
    with session_scope() as s:
        for gap in s.scalars(select(GapGap).where(GapGap.meeting_id == meeting_id)):
            if gap.template_item_key in keys:
                gap.suggested_question = general[gap.template_item_key].question


def test_a_freshly_detected_meeting_needs_no_refresh(team_id: str) -> None:
    """New data and refreshed old data come out of the same function."""
    meeting_id = seed(team_id)
    before = rows(meeting_id)

    assert service.refresh_questions(meeting_id) == 0
    assert rows(meeting_id) == before


def test_an_old_generic_question_is_recomputed_and_the_verdict_is_not(team_id: str) -> None:
    meeting_id = seed(team_id)
    fresh = rows(meeting_id)
    make_generic(meeting_id, "next_step", "dependency")
    assert "핵심 지표" not in str(rows(meeting_id)["next_step"]["question"])

    assert service.refresh_questions(meeting_id) == 2

    after = rows(meeting_id)
    assert after == fresh
    assert "핵심 지표" in str(after["next_step"]["question"])


def test_a_partial_gap_keeps_naming_its_own_topic(team_id: str) -> None:
    meeting_id = seed(team_id)
    make_generic(meeting_id, "risk")

    service.refresh_questions(meeting_id)

    assert str(rows(meeting_id)["risk"]["question"]).startswith("리스크")


def test_a_dry_run_counts_and_writes_nothing(team_id: str) -> None:
    meeting_id = seed(team_id)
    make_generic(meeting_id, "next_step")
    stale = rows(meeting_id)

    assert service.refresh_questions(meeting_id, apply=False) == 1
    assert rows(meeting_id) == stale


def test_a_dismissed_gap_is_refreshed_too(team_id: str) -> None:
    """Taking the dismissal back must show the same question as everything else."""
    meeting_id = seed(team_id)
    fresh = rows(meeting_id)["next_step"]["question"]
    make_generic(meeting_id, "next_step")
    with session_scope() as s:
        gap = s.get(GapGap, rows(meeting_id)["next_step"]["id"])
        assert gap is not None
        gap.dismissed_at = datetime.now(UTC)

    service.refresh_questions(meeting_id)

    assert rows(meeting_id)["next_step"]["question"] == fresh


def test_the_report_es_payload_and_the_agent_read_the_same_question(
    team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting_id = seed(team_id)
    make_generic(meeting_id, "next_step", "dependency")
    service.refresh_questions(meeting_id)
    stored = {row["id"]: row["question"] for row in rows(meeting_id).values()}

    with session_scope() as s:
        report = service.build_report(s, meeting_id)
        agent = tools.open_gaps(s, team_id=team_id, meeting_id=meeting_id)

    sent: list[Any] = []
    monkeypatch.setattr(service, "publish", lambda event, payload: sent.append(payload))
    service.publish_report(meeting_id)

    assert {g.id: g.suggested_question for g in report.gaps} == stored
    assert {g["id"]: g["suggested_question"] for g in sent[0]["gaps"]} == stored
    assert {i["id"]: i["body"] for i in agent["items"]} == {i: stored[i] for i in stored}


def test_the_backfill_visits_one_teams_meetings(team_id: str) -> None:
    other = _team("gap-refresh-other")
    try:
        mine, theirs = seed(team_id), seed(other)
        with session_scope() as s:
            assert service.refreshable_meeting_ids(s, team_id=team_id) == [mine]
            assert theirs in service.refreshable_meeting_ids(s)
    finally:
        with session_scope() as s:
            s.execute(delete(Team).where(Team.id == other))


def test_the_command_republishes_what_changed_and_is_safe_to_rerun(
    team_id: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The republish is what carries the new question to E's stored copy."""
    meeting_id = seed(team_id)
    make_generic(meeting_id, "next_step")
    queued: list[str] = []
    monkeypatch.setattr(cli, "make_celery_app", lambda **_: None)
    monkeypatch.setattr("autune_gap.enqueue.enqueue_publish_report", queued.append)

    assert cli.main(["--team", team_id, "--dry-run"]) == 0
    assert queued == []
    assert "1 question(s) would change" in capsys.readouterr().out

    assert cli.main(["--team", team_id]) == 0
    assert queued == [meeting_id]

    assert cli.main(["--team", team_id]) == 0
    assert queued == [meeting_id]
    assert "0 question(s) changed" in capsys.readouterr().out
