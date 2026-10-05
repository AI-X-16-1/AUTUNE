"""Briefing from a chat message to the stored row, on a real PostgreSQL.

``tests/test_briefing_subagent.py`` runs the graph over mock tools. This one is
for what mocks cannot show: the **real registry** (D's, B's and C's ``tools.py``)
answering over rows built the way the pipeline builds them, and the one seam
between modules that only the database proves:

    B ``service.team_agenda`` -> D ``briefs.store_team_agenda`` -> D ``brief_agenda``

with D's own ``compose_due_brief`` choosing the earlier meeting, C's gap on that
earlier meeting, and B's late item. The router is ``FakeRouter``: the point is
the data path, and it composes by echoing, so the text asserted below is what a
real router would be handed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import autune_context.models  # noqa: F401  (ctx_ tables)
import autune_extraction.models  # noqa: F401  (ext_ tables)
import autune_gap.models  # noqa: F401  (gap_ tables)
from autune_agent import router as routes
from autune_agent.models import AgentRun
from autune_agent.subagents.briefing.graph import (
    ACTIONS_TOOL,
    AGENDA_TOOL,
    GAPS_TOOL,
    RECAP_TOOL,
)
from autune_agent.testing import FakeRouter
from autune_context import briefs
from autune_context.config import get_settings as get_context_settings
from autune_context.models import CtxDecision, CtxDecisionVersion, CtxMeetingStatus
from autune_context.pipeline import reset_cache
from autune_core import (
    Meeting,
    Team,
    TeamMember,
    User,
    current_user,
    get_session,
)
from autune_extraction import service as extraction
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem, ExtExternalRef
from autune_gap.models import GapGap, GapTopic

NOW = datetime.now(tz=UTC)

PAST_TITLE = "주간 회의"
DECISION = "검색 정렬은 관련도순으로 바꾼다"
ISSUE = "결제 모듈 API 명세 정리"
LATE_ITEM = "API 스펙 문서 작성"
GAP_TITLE = "담당자와 기한이 정해지지 않았습니다"
QUESTION = "누가 언제까지 맡을까요?"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fake D models (the series match needs none, but the topic step would) and
    B's settings read from no ``.env``."""
    for knob in ("EMBEDDER", "RERANKER", "NLI"):
        monkeypatch.setenv(f"AUTUNE_CONTEXT_{knob}_IMPL", "fake")
    get_context_settings.cache_clear()
    reset_cache()
    monkeypatch.setattr(
        extraction,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )
    yield
    get_context_settings.cache_clear()
    reset_cache()


def _seed(session: Session) -> dict[str, str]:
    team = Team(name="브리핑팀")
    asker = User(email="briefing-asker@example.com", display_name="질문자")
    session.add_all([team, asker])
    session.flush()
    session.add(TeamMember(team_id=team.id, user_id=asker.id))

    past = Meeting(
        team_id=team.id,
        title=PAST_TITLE,
        status="complete",
        started_at=NOW - timedelta(days=7),
        expires_at=NOW + timedelta(days=90),
    )
    upcoming = Meeting(
        team_id=team.id,
        title=PAST_TITLE,
        status="scheduled",
        started_at=NOW + timedelta(minutes=10),
        expires_at=NOW + timedelta(days=90),
    )
    session.add_all([past, upcoming])
    session.flush()

    # D: the earlier meeting was analysed and recorded a decision.
    session.add(CtxMeetingStatus(meeting_id=past.id, topic_linking_done=True))
    thread = CtxDecision(team_id=team.id, topic_label="검색 정렬")
    session.add(thread)
    session.flush()
    session.add(
        CtxDecisionVersion(
            thread_id=thread.id,
            source_decision_id="dec_briefing",
            meeting_id=past.id,
            current_statement=DECISION,
            change_type="reversed",
            confidence=0.9,
            nli_version="test",
        )
    )

    # C: it left one gap open there.
    topic = GapTopic(meeting_id=past.id, label="검색", extractor_version="test")
    session.add(topic)
    session.add(
        GapGap(
            meeting_id=past.id,
            category="ownership",
            title=GAP_TITLE,
            severity="high",
            risk_score=0.9,
            suggested_question=QUESTION,
        )
    )

    # B: a late action item that became a Jira issue.
    item = ExtActionItem(
        meeting_id=past.id,
        description=LATE_ITEM,
        assignee_id=asker.id,
        due_date=date.today() - timedelta(days=2),
        status="todo",
        confidence=0.9,
        origin="user",
    )
    session.add(item)
    session.flush()
    session.add(
        ExtExternalRef(
            action_item_id=item.id,
            system="jira",
            meeting_id=past.id,
            external_id="AUT-7",
            url="https://autune.atlassian.net/browse/AUT-7",
        )
    )
    session.flush()
    return {"team": team.id, "asker": asker.id, "past": past.id, "upcoming": upcoming.id}


def _chat(session: Session, seed: dict[str, str], message: str) -> dict:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/agent")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, seed["asker"])
    app.dependency_overrides[routes.get_chat_router] = lambda: FakeRouter({"브리프": "briefing"})
    response = TestClient(app).post(
        "/api/agent/chat", json={"team_id": seed["team"], "message": message}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _hand_over_what_the_pipeline_would(session: Session, seed: dict[str, str]) -> None:
    """B publishes the team's agenda, D keeps it and composes the brief ten
    minutes before the start -- the two steps ``publish_team_agendas`` and
    ``send_brief`` take, without the broker."""
    agenda = extraction.team_agenda(session, seed["team"], now=NOW)
    assert [i.key for i in agenda.issues] == ["AUT-7"]  # B made it
    assert briefs.store_team_agenda(session, agenda)  # D kept it
    brief = briefs.compose_due_brief(session, seed["upcoming"], now=NOW, will_send=False)
    assert brief is not None
    assert brief.match_reason == briefs.SERIES  # D chose the earlier meeting


def test_a_scheduled_meeting_gets_the_whole_brief_from_the_real_tools(db_session: Session) -> None:
    seed = _seed(db_session)
    _hand_over_what_the_pipeline_would(db_session, seed)

    body = _chat(db_session, seed, f"{seed['upcoming']} 브리프 만들어줘")

    assert body["route"] == "briefing"
    assert body["outcome"] == "answered"
    assert (body["proposed"], body["executed"]) == (0, 0)
    sections = {item["title"]: item["body"] for item in body["items"]}
    assert list(sections) == [
        "지난 회의에서 이어받는 결정",
        "팀의 열린 Jira 이슈",
        "기한이 지났거나 다가온 액션 아이템",
        "지난 회의에서 닫히지 않은 갭",
    ]
    assert PAST_TITLE in sections["지난 회의에서 이어받는 결정"]
    assert f"{DECISION} (번복)" in sections["지난 회의에서 이어받는 결정"]
    assert "AUT-7" in sections["팀의 열린 Jira 이슈"]
    assert LATE_ITEM in sections["팀의 열린 Jira 이슈"]  # B's title reaches D's brief
    assert f"• {GAP_TITLE}\n  ↳ {QUESTION}" in sections["지난 회의에서 닫히지 않은 갭"]


def test_the_late_action_item_comes_from_b_even_when_it_became_a_jira_issue(
    db_session: Session,
) -> None:
    seed = _seed(db_session)
    _hand_over_what_the_pipeline_would(db_session, seed)

    body = _chat(db_session, seed, f"{seed['upcoming']} 브리프")

    sections = {item["title"]: item["body"] for item in body["items"]}
    assert f"• {LATE_ITEM}" in sections["기한이 지났거나 다가온 액션 아이템"]
    assert "기한 지남" in sections["기한이 지났거나 다가온 액션 아이템"]
    assert "읽지 못한" not in body["answer"]


def test_the_run_row_keeps_tool_names_and_ids_and_none_of_the_brief(db_session: Session) -> None:
    seed = _seed(db_session)
    _hand_over_what_the_pipeline_would(db_session, seed)

    body = _chat(db_session, seed, f"{seed['upcoming']} 브리프")

    db_session.expire_all()
    run = db_session.scalars(select(AgentRun).where(AgentRun.id == body["run_id"])).one()
    assert run.team_id == seed["team"]
    assert [s["tool"] for s in run.steps][:4] == [RECAP_TOOL, AGENDA_TOOL, GAPS_TOOL, ACTIONS_TOOL]
    assert run.proposed == [] and run.actions == []
    stored = json.dumps([run.steps, run.proposed, run.actions], ensure_ascii=False)
    for text in (PAST_TITLE, DECISION, ISSUE, LATE_ITEM, GAP_TITLE, QUESTION, "AUT-7"):
        assert text not in stored


def test_the_gap_read_is_about_the_earlier_meeting(db_session: Session) -> None:
    """C has no gap on the meeting that is about to start; the one it shows is the
    earlier meeting's, which only D knew to name."""
    seed = _seed(db_session)
    _hand_over_what_the_pipeline_would(db_session, seed)
    assert (
        db_session.scalars(select(GapGap).where(GapGap.meeting_id == seed["upcoming"])).first()
        is None
    )

    body = _chat(db_session, seed, f"{seed['upcoming']} 브리프")

    assert any(GAP_TITLE in i["body"] for i in body["items"])


def test_before_d_composes_the_brief_the_agenda_and_late_work_still_come(
    db_session: Session,
) -> None:
    seed = _seed(db_session)
    agenda = extraction.team_agenda(db_session, seed["team"], now=NOW)
    briefs.store_team_agenda(db_session, agenda)  # B and D ran; send_brief has not

    body = _chat(db_session, seed, f"{seed['upcoming']} 브리프")

    sections = {item["title"]: item["body"] for item in body["items"]}
    assert "10분 전에 정해집니다" in sections["지난 회의"]
    assert "팀의 열린 Jira 이슈" in sections
    assert "닫히지 않은 갭" not in " ".join(sections)  # nothing to ask C about yet


def test_another_teams_meeting_is_refused_as_missing(db_session: Session) -> None:
    seed = _seed(db_session)
    other = Team(name="다른팀")
    db_session.add(other)
    db_session.flush()
    theirs = Meeting(team_id=other.id, title="비밀 회의", status="scheduled")
    db_session.add(theirs)
    db_session.flush()

    body = _chat(db_session, seed, f"{theirs.id} 브리프")

    assert body["items"] == []
    assert "비밀 회의" not in json.dumps(body, ensure_ascii=False)
