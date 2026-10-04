"""A meeting's minutes per project, to Notion, Slack and Jira (2026-10-04).

No network: fakes stand in for the three tools and record what they were sent.
The rules under test: only confirmed rows go, as "팀-프로젝트-날짜"; 미분류 is
counted and not sent; sending again updates the same copies; a tool that is not
connected is reported, not tried; and one tool failing never stops the others.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import (
    Base,
    Meeting,
    Participant,
    Team,
    TeamMember,
    User,
    Utterance,
    get_session,
)
from autune_core.errors import AutuneError
from autune_extraction import project_send, projects, service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import (
    ExtActionItem,
    ExtCalendarCleanup,
    ExtDecision,
    ExtDecisionReview,
    ExtMinutesEvent,
    ExtProject,
    ExtProjectSend,
)
from autune_extraction.router import router
from autune_integrations.errors import PermanentIntegrationError

from .conftest import sign_in

MEETING = "mtg_1"
TEAM = "team_1"
PREFIX = "/api/extraction"


class FakeNotion:
    def __init__(self) -> None:
        self.pages: list[dict[str, Any]] = []
        self.trashed: list[str] = []

    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        self.pages.append(json)
        return {"id": f"page_{len(self.pages)}"}

    def trash_page(self, page_id: str) -> bool:
        self.trashed.append(page_id)
        return True


class FakeSlack:
    def __init__(self) -> None:
        self.posted: list[tuple[str, str]] = []
        self.updated: list[tuple[str, str, str]] = []

    def post_message(self, channel: str, text: str) -> str:
        self.posted.append((channel, text))
        return f"17000.{len(self.posted)}"

    def update_message(self, channel: str, ts: str, text: str) -> None:
        self.updated.append((channel, ts, text))


class FakeJira:
    def __init__(self, *, fail: bool = False) -> None:
        self.created: list[tuple[str, str, str]] = []
        self.updated: list[str] = []
        self.fail = fail

    def create_task(self, key: str, summary: str, *, description: str = "") -> str:
        if self.fail:
            raise PermanentIntegrationError("jira refused")
        self.created.append((key, summary, description))
        return f"{key}-{len(self.created)}"

    def update_task(self, issue_key: str, summary: str, **_: Any) -> bool:
        self.updated.append(issue_key)
        return True


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, Team, TeamMember, Participant, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Team(id=TEAM, name="제품팀"))
        s.add(
            Meeting(
                id=MEETING,
                team_id=TEAM,
                title="주간 회의",
                started_at=datetime(2026, 10, 1, 1, tzinfo=UTC),
            )
        )
        s.add(ExtProject(id="prj_a", team_id=TEAM, name="Autune", aliases=""))
        s.add(ExtProject(id="prj_b", team_id=TEAM, name="App", aliases="", jira_project_key="APP"))
        s.add(ExtProject(id="prj_c", team_id=TEAM, name="빈 프로젝트", aliases=""))
        rows = [
            ("dec_ok", "배포는 금요일로 한다", "prj_a", "confirmed"),
            ("dec_wait", "아직 정하지 않은 결정", "prj_a", None),
            ("dec_app", "앱 아이콘을 바꾼다", "prj_b", "confirmed"),
            ("dec_loose", "어디에도 없는 결정", None, "confirmed"),
        ]
        for decision_id, statement, project, status in rows:
            s.add(
                ExtDecision(
                    id=decision_id,
                    meeting_id=MEETING,
                    statement=statement,
                    confidence=0.9,
                    origin="model",
                    project_id=project,
                )
            )
            if status:
                s.add(
                    ExtDecisionReview(
                        decision_id=decision_id,
                        meeting_id=MEETING,
                        status=status,
                        reviewed_at=datetime(2026, 10, 1, tzinfo=UTC),
                    )
                )
        s.add(
            ExtActionItem(
                id="act_ok",
                meeting_id=MEETING,
                description="릴리스 노트 정리",
                assignee_label="민경",
                due_date=date(2026, 10, 3),
                status="todo",
                confidence=0.9,
                origin="model",
                project_id="prj_a",
            )
        )
        s.add(
            ExtActionItem(
                id="act_wait",
                meeting_id=MEETING,
                description="확인 전 할 일",
                status="needs_confirmation",
                confidence=0.9,
                origin="model",
                project_id="prj_a",
            )
        )
        s.flush()
        yield s


def test_each_project_with_something_confirmed_gets_its_minutes(session: Session) -> None:
    found, unsorted = project_send.minutes(session, MEETING)

    assert [m.title for m in found] == ["제품팀-Autune-2026-10-01", "제품팀-App-2026-10-01"]
    autune = found[0]
    assert autune.decisions == ("배포는 금요일로 한다",)
    assert autune.items == ("릴리스 노트 정리 (담당 민경, 기한 2026-10-03)",)
    assert "확인 전 할 일" not in autune.text and "아직 정하지 않은" not in autune.text
    assert unsorted == 1, "the confirmed decision with no project"


def clients(**over: Any) -> tuple[project_send.Clients, FakeNotion, FakeSlack, FakeJira]:
    notion, slack, jira = FakeNotion(), FakeSlack(), over.pop("jira", FakeJira())
    return (
        project_send.Clients(
            notion=(notion, "db_minutes"), slack=(slack, "C_TEAM"), jira=(jira, "TEAM")
        ),
        notion,
        slack,
        jira,
    )


def test_minutes_go_to_all_three_tools(session: Session) -> None:
    tools, notion, slack, jira = clients()

    sent, _ = project_send.send(session, MEETING, ["notion", "slack", "jira"], tools)

    assert {(s.project_name, s.target, s.outcome) for s in sent} == {
        (p, t, "created") for p in ("Autune", "App") for t in ("notion", "slack", "jira")
    }
    page = notion.pages[0]
    assert page["parent"] == {"database_id": "db_minutes"}
    assert page["properties"]["제목"]["title"][0]["text"]["content"] == "제품팀-Autune-2026-10-01"
    assert page["properties"]["날짜"] == {"date": {"start": "2026-10-01"}}
    assert slack.posted[0][0] == "C_TEAM"
    assert slack.posted[0][1].startswith("제품팀-Autune-2026-10-01\n\n결정\n- 배포는 금요일로 한다")
    # App names its own Jira project; Autune falls back to the team's.
    assert [(key, summary) for key, summary, _ in jira.created] == [
        ("TEAM", "제품팀-Autune-2026-10-01"),
        ("APP", "제품팀-App-2026-10-01"),
    ]


def test_sending_again_updates_the_same_copies(session: Session) -> None:
    tools, notion, slack, jira = clients()
    project_send.send(session, MEETING, ["notion", "slack", "jira"], tools)

    sent, _ = project_send.send(session, MEETING, ["notion", "slack", "jira"], tools)

    assert {s.outcome for s in sent} == {"updated"}
    assert notion.trashed == ["page_1", "page_2"]
    assert [ts for _, ts, _ in slack.updated] == ["17000.1", "17000.2"]
    assert jira.updated == ["TEAM-1", "APP-2"]
    assert len(slack.posted) == 2 and len(jira.created) == 2


def test_a_tool_not_connected_is_reported_and_not_tried(session: Session) -> None:
    sent, _ = project_send.send(session, MEETING, ["slack"], project_send.Clients())

    assert {s.outcome for s in sent} == {"not_connected"}
    assert session.query(ExtProjectSend).count() == 0


def test_one_tool_failing_never_stops_the_others(session: Session) -> None:
    tools, _, slack, _ = clients(jira=FakeJira(fail=True))

    sent, _ = project_send.send(session, MEETING, ["slack", "jira"], tools)

    outcomes = {(s.project_name, s.target): s.outcome for s in sent}
    assert outcomes[("Autune", "slack")] == "created"
    assert outcomes[("Autune", "jira")] == "failed"
    assert len(slack.posted) == 2


def test_the_route_reports_each_copy(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    # No tool connected (team_integrations is Postgres-only, JSONB).
    monkeypatch.setattr(tasks, "load_integration", lambda *_: None)
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session, team_id=TEAM)
    client = TestClient(app)

    report = client.post(
        f"{PREFIX}/summary/{MEETING}/projects/send", json={"targets": ["slack", "notion"]}
    ).json()

    assert report["unsorted"] == 1
    assert {(r["project_name"], r["target"], r["outcome"]) for r in report["results"]} == {
        (p, t, "not_connected") for p in ("Autune", "App") for t in ("slack", "notion")
    }
    assert (
        client.post(f"{PREFIX}/summary/{MEETING}/projects/send", json={"targets": []}).status_code
        == 422
    )


# --- the sender's own calendar ------------------------------------------------------


class FakeCalendar:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[str] = []
        self.gone: set[str] = set()

    def create_all_day_event(
        self,
        calendar_id: str,
        summary: str,
        day: date,
        *,
        description: str = "",
        private: dict[str, str] | None = None,
    ) -> str:
        self.created.append(
            {"calendar": calendar_id, "summary": summary, "day": day, "private": private}
        )
        return f"evt_{len(self.created)}"

    def update_all_day_event(
        self, calendar_id: str, event_id: str, summary: str, day: date, *, description: str = ""
    ) -> bool:
        if event_id in self.gone:
            return False
        self.updated.append(event_id)
        return True


def calendar_clients(calendar: FakeCalendar) -> project_send.Clients:
    return project_send.Clients(calendar=(calendar, "primary", "user_sender"))


def test_minutes_go_to_the_senders_own_calendar_on_the_meeting_day(session: Session) -> None:
    calendar = FakeCalendar()

    sent, _ = project_send.send(session, MEETING, ["calendar"], calendar_clients(calendar))

    assert {(s.project_name, s.outcome) for s in sent} == {
        ("Autune", "created"),
        ("App", "created"),
    }
    first = calendar.created[0]
    assert first["summary"] == "제품팀-Autune-2026-10-01"
    assert first["day"] == date(2026, 10, 1)
    # Not the due-date events' tag: the read-back must never take it for an item.
    assert first["private"] == {"autune_minutes": "1"}
    rows = session.query(ExtMinutesEvent).all()
    assert {(r.user_id, r.event_id) for r in rows} == {
        ("user_sender", "evt_1"),
        ("user_sender", "evt_2"),
    }


def test_sending_again_updates_the_event_or_replaces_a_deleted_one(session: Session) -> None:
    calendar = FakeCalendar()
    project_send.send(session, MEETING, ["calendar"], calendar_clients(calendar))
    calendar.gone.add("evt_2")

    sent, _ = project_send.send(session, MEETING, ["calendar"], calendar_clients(calendar))

    assert {s.outcome for s in sent} == {"updated"}
    assert calendar.updated == ["evt_1"]
    assert len(calendar.created) == 3, "the event deleted by hand is made again"


def test_a_meeting_with_no_day_gets_no_event(session: Session) -> None:
    meeting = session.get(Meeting, MEETING)
    assert meeting is not None
    meeting.started_at = None
    session.flush()

    sent, _ = project_send.send(session, MEETING, ["calendar"], calendar_clients(FakeCalendar()))

    assert {s.outcome for s in sent} == {"no_date"}


def test_no_calendar_connected_is_reported(session: Session) -> None:
    sent, _ = project_send.send(session, MEETING, ["calendar"], project_send.Clients())

    assert {s.outcome for s in sent} == {"not_connected"}


def test_deleting_a_project_queues_its_events_for_removal(session: Session) -> None:
    project_send.send(session, MEETING, ["calendar"], calendar_clients(FakeCalendar()))

    projects.delete_project(session, TEAM, "prj_a")

    queued = {(q.user_id, q.event_id) for q in session.query(ExtCalendarCleanup).all()}
    assert queued == {("user_sender", "evt_1")}


def test_an_expiring_meeting_queues_its_minutes_events(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_send.send(session, MEETING, ["calendar"], calendar_clients(FakeCalendar()))

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session

    monkeypatch.setattr(tasks, "session_scope", scope)
    tasks.queue_meeting_calendar_events(MEETING)

    queued = {(q.user_id, q.event_id) for q in session.query(ExtCalendarCleanup).all()}
    assert queued == {("user_sender", "evt_1"), ("user_sender", "evt_2")}
