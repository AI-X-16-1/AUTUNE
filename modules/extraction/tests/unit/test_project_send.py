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
from autune_extraction import project_send, service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import (
    ExtActionItem,
    ExtDecision,
    ExtDecisionReview,
    ExtProject,
    ExtProjectSend,
    ExtProjectSendCleanup,
)
from autune_extraction.router import router
from autune_integrations.errors import PermanentIntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS, strings_in

from .conftest import sign_in

MEETING = "mtg_1"
TEAM = "team_1"
PREFIX = "/api/extraction"


class FakeNotion:
    """Records every request in order, with the page each one touched."""

    def __init__(self, *, fail_appends: bool = False) -> None:
        self.pages: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.blocks: dict[str, list[str]] = {}
        self.bodies: list[dict[str, Any]] = []
        self.trashed: list[str] = []
        self.retitled: list[str] = []
        self.fail_appends = fail_appends

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Any = None,  # noqa: A002
    ) -> dict[str, Any]:
        self.calls.append((method, path))
        if json is not None:
            self.bodies.append(json)
        if method == "POST" and path == "/pages":
            self.pages.append(json)
            page_id = f"page_{len(self.pages)}"
            self.blocks[page_id] = [f"{page_id}_b{n}" for n in range(len(json["children"]))]
            return {"id": page_id}
        if method == "PATCH" and path.endswith("/children"):
            if self.fail_appends:
                raise PermanentIntegrationError("notion refused")
            page_id = path.split("/")[2]
            self.blocks[page_id] += [f"{page_id}_x{n}" for n in range(len(json["children"]))]
            return {}
        if method == "GET":
            page_id = path.split("/")[2]
            return {"results": [{"id": b} for b in self.blocks.get(page_id, [])]}
        if method == "DELETE":
            block = path.split("/")[2]
            for listed in self.blocks.values():
                if block in listed:
                    listed.remove(block)
            return {}
        raise AssertionError((method, path))

    def update_page(self, page_id: str, properties: dict[str, Any]) -> None:
        self.calls.append(("retitle", page_id))
        self.retitled.append(page_id)
        self.bodies.append(properties)

    def page_state(self, page_id: str) -> str:
        return "live"

    def trash_page(self, page_id: str) -> bool:
        self.calls.append(("trash", page_id))
        self.trashed.append(page_id)
        return True

    def close(self) -> None:
        pass


class FakeSlack:
    def __init__(self, *, fail: bool = False) -> None:
        self.posted: list[tuple[str, str]] = []
        self.updated: list[tuple[str, str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self.fail = fail

    def post_message(self, channel: str, text: str) -> str:
        if self.fail:
            raise RuntimeError("not an integration error")
        self.posted.append((channel, text))
        return f"17000.{len(self.posted)}"

    def update_message(self, channel: str, ts: str, text: str) -> None:
        self.updated.append((channel, ts, text))

    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        assert (method, path) == ("POST", "/chat.delete")
        self.deleted.append((json["channel"], json["ts"]))
        return {"ok": True}

    def close(self) -> None:
        pass


class FakeJira:
    def __init__(self, *, fail: bool = False) -> None:
        self.created: list[tuple[str, str, str]] = []
        self.updated: list[str] = []
        self.descriptions: list[str] = []
        self.closed: list[str] = []
        self.fail = fail

    def create_task(self, key: str, summary: str, *, description: str = "") -> str:
        if self.fail:
            raise PermanentIntegrationError("jira refused")
        self.created.append((key, summary, description))
        return f"{key}-{len(self.created)}"

    def update_task(self, issue_key: str, summary: str, **kw: Any) -> bool:
        self.updated.append(issue_key)
        self.descriptions.append(kw.get("description") or "")
        return True

    def move_to_category(self, issue_key: str, category: str) -> bool:
        self.closed.append(issue_key)
        return True

    def close(self) -> None:
        pass


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
    # The new page first, then the old one emptied and trashed: a failed send
    # never leaves the team without its page.
    assert notion.calls.index(("POST", "/pages")) < notion.calls.index(("retitle", "page_1"))
    assert notion.calls.index(("retitle", "page_1")) < notion.calls.index(("trash", "page_1"))
    assert notion.blocks["page_1"] == [], "nothing left in the trash to read"
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


def _withdraw(session: Session, decision_id: str) -> None:
    review = session.get(ExtDecisionReview, decision_id)
    assert review is not None
    review.status = "rejected"
    session.flush()


def test_a_project_left_with_nothing_is_retracted_when_sent_again(session: Session) -> None:
    tools, notion, slack, jira = clients()
    project_send.send(session, MEETING, ["notion", "slack", "jira"], tools)
    _withdraw(session, "dec_app")  # App's only confirmed row

    sent, _ = project_send.send(session, MEETING, ["notion", "slack", "jira"], tools)

    app = {(s.target, s.outcome) for s in sent if s.project_id == "prj_b"}
    assert app == {("notion", "retracted"), ("slack", "retracted"), ("jira", "retracted")}
    assert "page_2" in notion.trashed and notion.blocks["page_2"] == []
    assert ("C_TEAM", "17000.2") in slack.deleted
    assert "APP-2" in jira.closed
    assert session.query(ExtProjectSend).filter_by(project_id="prj_b").count() == 0


def test_a_change_after_sending_rewrites_or_retracts_the_copies(session: Session) -> None:
    tools, notion, slack, jira = clients()
    project_send.send(session, MEETING, ["slack", "jira"], tools)
    _withdraw(session, "dec_ok")  # Autune keeps its item
    _withdraw(session, "dec_app")  # App has nothing left

    refreshed = project_send.refresh(session, MEETING, tools)

    outcomes = {(s.project_id, s.target): s.outcome for s in refreshed}
    assert outcomes == {
        ("prj_a", "slack"): "updated",
        ("prj_a", "jira"): "updated",
        ("prj_b", "slack"): "retracted",
        ("prj_b", "jira"): "retracted",
    }
    rewritten = slack.updated[-1][2]
    assert "배포는 금요일" not in rewritten and "릴리스 노트 정리" in rewritten
    assert notion.pages == [], "refresh makes no copy that was not there"


def test_refresh_without_copies_does_nothing(session: Session) -> None:
    tools, notion, slack, _ = clients()

    assert project_send.refresh(session, MEETING, tools) == []
    assert not notion.calls and not slack.posted


def test_an_unexpected_error_costs_that_copy_only(session: Session) -> None:
    tools, notion, _, _ = clients()
    tools.slack = (FakeSlack(fail=True), "C_TEAM")

    sent, _ = project_send.send(session, MEETING, ["notion", "slack"], tools)

    outcomes = {(s.project_id, s.target): s.outcome for s in sent}
    assert outcomes[("prj_a", "notion")] == "created"
    assert outcomes[("prj_a", "slack")] == "failed"
    kept = {(r.project_id, r.target) for r in session.query(ExtProjectSend)}
    assert kept == {("prj_a", "notion"), ("prj_b", "notion")}, "sent copies stay recorded"


def test_long_minutes_go_to_notion_in_requests_under_the_limit(session: Session) -> None:
    for n in range(120):
        session.add(
            ExtActionItem(
                id=f"act_long{n}",
                meeting_id=MEETING,
                description=f"{n}번째 아주 긴 할 일 설명입니다 " * 3,
                status="todo",
                confidence=0.9,
                origin="model",
                project_id="prj_a",
            )
        )
    session.flush()
    tools, notion, slack, jira = clients()

    sent, _ = project_send.send(session, MEETING, ["notion", "slack", "jira"], tools)

    assert {s.outcome for s in sent} == {"created"}
    assert all(len("".join(strings_in(b))) < MAX_OUTBOUND_CHARS for b in notion.bodies)
    assert len(notion.blocks["page_1"]) == 124, "two headings and every line, in several requests"
    assert len(notion.calls) > 1
    assert len(slack.posted[0][1]) < MAX_OUTBOUND_CHARS
    assert "Autune에서 볼 수 있습니다" in slack.posted[0][1]
    assert all(len(d) < MAX_OUTBOUND_CHARS for _, _, d in jira.created)


def test_a_page_whose_appends_fail_is_taken_back(session: Session) -> None:
    for n in range(120):
        session.add(
            ExtActionItem(
                id=f"act_long{n}",
                meeting_id=MEETING,
                description=f"{n}번째 아주 긴 할 일 설명입니다 " * 3,
                status="todo",
                confidence=0.9,
                origin="model",
                project_id="prj_a",
            )
        )
    session.flush()
    tools, _, _, _ = clients()
    notion = FakeNotion(fail_appends=True)
    tools.notion = (notion, "db_minutes")

    sent, _ = project_send.send(session, MEETING, ["notion"], tools)

    assert {(s.project_id, s.outcome) for s in sent} == {("prj_a", "failed"), ("prj_b", "created")}
    assert "page_1" in notion.trashed, "no half a page left live"
    assert session.query(ExtProjectSend).filter_by(project_id="prj_a").count() == 0


def test_a_deleted_meeting_or_project_queues_its_copies(session: Session) -> None:
    tools, _, _, _ = clients()
    project_send.send(session, MEETING, ["notion", "slack"], tools)

    assert project_send.queue_project(session, "prj_b") == 2
    assert project_send.queue_meeting(session, MEETING) == 4
    queued = {(r.target, r.external_id) for r in session.query(ExtProjectSendCleanup)}
    assert queued == {
        ("notion", "page_1"),
        ("notion", "page_2"),
        ("slack", "C_TEAM:17000.1"),
        ("slack", "C_TEAM:17000.2"),
    }, "queued once, however often"


def test_the_drain_retracts_queued_copies(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools, notion, slack, _ = clients()
    project_send.send(session, MEETING, ["notion", "slack"], tools)
    project_send.queue_meeting(session, MEETING)

    @contextmanager
    def same_session() -> Iterator[Session]:
        yield session

    monkeypatch.setattr(tasks, "session_scope", same_session)
    monkeypatch.setattr(tasks, "_project_clients", lambda *_: tools)

    assert tasks.drain_project_send_cleanup() == 4
    assert sorted(notion.trashed) == ["page_1", "page_2"]
    assert sorted(slack.deleted) == [("C_TEAM", "17000.1"), ("C_TEAM", "17000.2")]
    assert session.query(ExtProjectSendCleanup).count() == 0


def test_a_tool_no_longer_connected_is_given_up_on_after_some_tries(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session.add(ExtProjectSendCleanup(team_id=TEAM, target="slack", external_id="C:1"))
    session.flush()

    @contextmanager
    def same_session() -> Iterator[Session]:
        yield session

    monkeypatch.setattr(tasks, "session_scope", same_session)
    monkeypatch.setattr(tasks, "_project_clients", lambda *_: project_send.Clients())

    for _ in range(tasks.CLEANUP_MAX_ATTEMPTS - 1):
        assert tasks.drain_project_send_cleanup() == 0
    assert session.query(ExtProjectSendCleanup).count() == 1
    tasks.drain_project_send_cleanup()
    assert session.query(ExtProjectSendCleanup).count() == 0


def test_the_meetings_that_sent_minutes_are_found_from_their_rows(session: Session) -> None:
    tools, _, _, _ = clients()
    assert project_send.meetings_with_sends(session, ["act_ok"], ["dec_ok"]) == set()
    project_send.send(session, MEETING, ["slack"], tools)

    assert project_send.meetings_with_sends(session, ["act_ok"], []) == {MEETING}
    assert project_send.meetings_with_sends(session, [], ["dec_app"]) == {MEETING}
