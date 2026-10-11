"""``agent.live_research_notes``: a meeting's finished live notes, for Research."""

from __future__ import annotations

from sqlalchemy.orm import Session

from autune_agent.live.notes import live_research_notes
from autune_agent.main.own_tools import collect_own_tools
from autune_agent.models import AgentLiveResearch
from autune_core import Meeting, Team


def _doc(session: Session, team: dict[str, str], status: str, body: str | None) -> None:
    session.add(
        AgentLiveResearch(
            team_id=team["team"],
            meeting_id=team["meeting"],
            origin="auto",
            status=status,
            question=f"질문 {status}",
            body=body,
        )
    )
    session.commit()


def test_it_lists_done_notes_with_their_answer_line(session: Session, team: dict[str, str]) -> None:
    _doc(session, team, "done", "보통 1~2일입니다.\n- 웹: 애플 기준")
    _doc(session, team, "running", None)
    _doc(session, team, "failed", None)

    found = live_research_notes(session, team["team"], team["meeting"])

    assert found["ok"] is True
    assert [(i["title"], i["body"]) for i in found["items"]] == [("질문 done", "보통 1~2일입니다.")]


def test_another_teams_meeting_is_not_found(session: Session, team: dict[str, str]) -> None:
    other = Team(name="다른 팀")
    session.add(other)
    session.flush()
    meeting = Meeting(team_id=other.id, title="남의 회의")
    session.add(meeting)
    session.commit()

    found = live_research_notes(session, team["team"], meeting.id)

    assert found["ok"] is False


def test_it_is_one_of_the_layers_own_tools() -> None:
    assert "agent.live_research_notes" in collect_own_tools()
