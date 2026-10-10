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
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

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
    ExtProjectRefreshOwed,
    ExtProjectSend,
    ExtProjectSendCleanup,
)
from autune_extraction.pipeline import FakeClassifier, FakeNli
from autune_extraction.router import router
from autune_extraction.schemas import DecisionReviewUpdate
from autune_integrations.errors import PermanentIntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS, check_outbound, strings_in
from autune_integrations.slack import SlackClient

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


class SlackBehindTheCheck(FakeSlack):
    """Checks a message the way ``SlackClient`` does before it leaves: the
    whole request body through ``check_outbound`` (``HttpClient.request``)."""

    def post_message(self, channel: str, text: str) -> str:
        check_outbound(
            {"channel": channel, "text": text},
            destination="slack",
            addressing=SlackClient.addressing,
        )
        return super().post_message(channel, text)


def _held_minutes(session: Session) -> tuple[list[project_send.Sent], FakeSlack, list[dict]]:
    """Autune's confirmed decision, its rewording carrying a phone number, sent.

    No save writes such a rewording any more -- typed text is screened when it
    is stored (#1130) -- so the number is put on the row as one stored before
    that rule holds it. The outbound check is what is left to stop it."""
    service.review_decision(
        session,
        session.get(ExtDecision, "dec_ok"),  # type: ignore[arg-type]
        DecisionReviewUpdate(statement="배포 문의는 담당자에게 한다"),
    )
    review = session.get(ExtDecisionReview, "dec_ok")
    assert review is not None
    review.statement = "배포 문의는 010-1234-5678 로 한다"
    session.flush()
    slack = SlackBehindTheCheck()
    tools = project_send.Clients(slack=(slack, "C_TEAM"))
    with capture_logs() as logs:
        sent, _ = project_send.send(session, MEETING, ["slack"], tools)
    return sent, slack, logs


def test_minutes_the_outbound_check_refuses_are_held_and_say_so(session: Session) -> None:
    """Reported as ``failed`` before, which reads as "try again" -- and trying
    again meets the same refusal. The other project's copy still goes."""
    sent, slack, logs = _held_minutes(session)

    outcomes = {(s.project_name, s.target): s.outcome for s in sent}
    assert outcomes == {("Autune", "slack"): "held", ("App", "slack"): "created"}
    assert [text for _, text in slack.posted] and all(
        "1234-5678" not in text for _, text in slack.posted
    )
    held = [entry for entry in logs if entry["event"].endswith("blocked_by_privacy_guard")]
    assert held == [
        {
            "event": "extraction_project_send_blocked_by_privacy_guard",
            "log_level": "warning",
            "meeting_id": MEETING,
            "project_id": "prj_a",
            "target": "slack",
        }
    ]
    assert "1234-5678" not in str(logs)


def test_held_minutes_are_still_a_copy_left_behind(session: Session) -> None:
    """As they were while they were called ``failed``: a refresh that held a
    copy back has not brought every copy in line."""
    sent, _, _ = _held_minutes(session)

    assert not project_send.in_line(sent)
    assert project_send.in_line([s for s in sent if s.outcome != "held"])


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
    """Google Calendar's events endpoint, as ``project_send`` calls it."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[str] = []
        self.deleted: list[str] = []
        self.gone: set[str] = set()

    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        if method == "POST":
            self.created.append(
                {
                    "calendar": path.split("/")[2],
                    "summary": json["summary"],
                    "description": json["description"],
                    "day": date.fromisoformat(json["start"]["date"]),
                    "private": json.get("extendedProperties", {}).get("private"),
                    "transparency": json.get("transparency"),
                }
            )
            return {"id": f"evt_{len(self.created)}"}
        event_id = path.rsplit("/", 1)[1]
        if event_id in self.gone:
            return {"status": "cancelled"}  # Google keeps a deleted event a while
        self.updated.append(event_id)
        return {"status": "confirmed", "description": json["description"]}

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        self.deleted.append(event_id)


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
    assert first["transparency"] == "transparent", "a note, not a busy day"
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


def test_a_calendar_that_did_not_answer_is_failed_not_unconnected(session: Session) -> None:
    sent, _ = project_send.send(
        session, MEETING, ["calendar"], project_send.Clients(calendar_failed=True)
    )

    assert {s.outcome for s in sent} == {"failed"}
    assert session.query(ExtMinutesEvent).count() == 0


def test_a_project_left_with_nothing_loses_its_event_when_sent_again(session: Session) -> None:
    calendar = FakeCalendar()
    project_send.send(session, MEETING, ["calendar"], calendar_clients(calendar))
    _withdraw(session, "dec_app")

    sent, _ = project_send.send(session, MEETING, ["calendar"], calendar_clients(calendar))

    assert ("prj_b", "retracted") in {(s.project_id, s.outcome) for s in sent}
    assert calendar.deleted == ["evt_2"]
    assert {r.project_id for r in session.query(ExtMinutesEvent)} == {"prj_a"}


def test_a_change_after_sending_reaches_the_senders_calendar(session: Session) -> None:
    calendar = FakeCalendar()
    project_send.send(session, MEETING, ["calendar"], calendar_clients(calendar))
    _withdraw(session, "dec_ok")
    _withdraw(session, "dec_app")
    asked: list[str] = []

    def calendar_for(user_id: str) -> tuple[Any, str] | None:
        asked.append(user_id)
        return calendar, "primary"

    refreshed = project_send.refresh(session, MEETING, project_send.Clients(), calendar_for)

    assert {(s.project_id, s.outcome) for s in refreshed} == {
        ("prj_a", "updated"),
        ("prj_b", "retracted"),
    }
    assert set(asked) == {"user_sender"}, "only through the owner's own grant"
    assert calendar.deleted == ["evt_2"]
    assert session.query(ExtMinutesEvent).count() == 1


def test_an_owner_not_reachable_keeps_the_event_for_later(session: Session) -> None:
    project_send.send(session, MEETING, ["calendar"], calendar_clients(FakeCalendar()))
    _withdraw(session, "dec_app")

    refreshed = project_send.refresh(session, MEETING, project_send.Clients(), lambda _: None)

    # Autune's event already says its minutes: nothing to reach its owner for.
    assert {(s.project_id, s.outcome) for s in refreshed} == {
        ("prj_a", "unchanged"),
        ("prj_b", "not_connected"),
    }
    assert project_send.in_line(refreshed) is False, "App's event is still owed"
    assert session.query(ExtMinutesEvent).count() == 2


def test_an_event_that_already_says_the_minutes_asks_for_no_grant(session: Session) -> None:
    """A refresh after every change must not refresh a person's Google token,
    or write to their calendar, for minutes that did not change (#787 review)."""
    calendar = FakeCalendar()
    project_send.send(session, MEETING, ["calendar"], calendar_clients(calendar))
    asked: list[str] = []

    def calendar_for(user_id: str) -> tuple[Any, str] | None:
        asked.append(user_id)
        return calendar, "primary"

    refreshed = project_send.refresh(session, MEETING, project_send.Clients(), calendar_for)

    assert {s.outcome for s in refreshed} == {"unchanged"} and len(refreshed) == 2
    assert asked == []
    assert len(calendar.created) == 2 and calendar.updated == [] and calendar.deleted == []


# --- a refresh can be repeated, and is until it has worked (#787 review) -----------


def test_a_refresh_leaves_a_copy_that_already_says_the_minutes_alone(session: Session) -> None:
    tools, notion, slack, jira = clients()
    project_send.send(session, MEETING, ["notion", "slack", "jira"], tools)

    refreshed = project_send.refresh(session, MEETING, tools)

    assert {s.outcome for s in refreshed} == {"unchanged"} and len(refreshed) == 6
    assert len(notion.pages) == 2 and notion.trashed == [], "no new page for the same minutes"
    assert slack.updated == [] and jira.updated == []


def test_a_refresh_rewrites_only_the_copies_whose_minutes_changed(session: Session) -> None:
    tools, _, slack, _ = clients()
    project_send.send(session, MEETING, ["slack"], tools)
    _withdraw(session, "dec_ok")  # Autune's minutes change; App's do not

    refreshed = project_send.refresh(session, MEETING, tools)

    assert {(s.project_id, s.outcome) for s in refreshed} == {
        ("prj_a", "updated"),
        ("prj_b", "unchanged"),
    }
    assert [ts for _, ts, _ in slack.updated] == ["17000.1"]


class SlackDownForEdits(FakeSlack):
    def update_message(self, channel: str, ts: str, text: str) -> None:
        raise PermanentIntegrationError("slack refused")


def test_a_copy_whose_rewrite_failed_is_still_behind_at_the_next_refresh(
    session: Session,
) -> None:
    """The digest moves only with a write that went: a failed copy must not
    look in line afterwards."""
    tools, _, slack, _ = clients()
    project_send.send(session, MEETING, ["slack"], tools)
    _withdraw(session, "dec_ok")
    tools.slack = (SlackDownForEdits(), "C_TEAM")

    failed = project_send.refresh(session, MEETING, tools)

    assert {(s.project_id, s.outcome) for s in failed} == {
        ("prj_a", "failed"),
        ("prj_b", "unchanged"),
    }
    assert project_send.in_line(failed) is False

    tools.slack = (slack, "C_TEAM")
    again = project_send.refresh(session, MEETING, tools)

    assert {(s.project_id, s.outcome) for s in again} == {
        ("prj_a", "updated"),
        ("prj_b", "unchanged"),
    }
    assert project_send.in_line(again) is True
    assert "배포는 금요일" not in slack.updated[-1][2]


def test_a_refresh_takes_the_copies_in_the_order_a_send_does(session: Session) -> None:
    """Each row's lock is held to the end of the transaction, so a send and a
    refresh that took one meeting's rows in different orders could deadlock."""
    tools, _, _, _ = clients()
    sent, _ = project_send.send(session, MEETING, ["notion", "slack", "jira"], tools)
    for row in session.query(ExtProjectSend):
        row.content_digest = None  # every copy behind
    session.flush()

    refreshed = project_send.refresh(session, MEETING, tools)

    assert [(s.project_id, s.target) for s in refreshed] == [(s.project_id, s.target) for s in sent]
    assert [s.target for s in refreshed[:3]] == ["notion", "slack", "jira"]


class NotionThatKeepsHalfAPage(FakeNotion):
    """Appends fail, and so does taking the page back."""

    def __init__(self) -> None:
        super().__init__(fail_appends=True)

    def update_page(self, page_id: str, properties: dict[str, Any]) -> None:
        raise PermanentIntegrationError("notion refused the retraction too")


def test_half_a_page_that_cannot_be_taken_back_is_queued_for_cleanup(session: Session) -> None:
    """It is live, holds the minutes' first lines, and no row names it: without
    the queue nothing would ever find it again."""
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
    tools.notion = (NotionThatKeepsHalfAPage(), "db_minutes")

    sent, _ = project_send.send(session, MEETING, ["notion"], tools)

    assert {(s.project_id, s.outcome) for s in sent} == {("prj_a", "failed"), ("prj_b", "created")}
    assert session.query(ExtProjectSend).filter_by(project_id="prj_a").count() == 0
    queued = {(r.target, r.external_id) for r in session.query(ExtProjectSendCleanup)}
    assert queued == {("notion", "page_1")}


@pytest.fixture
def in_tasks(session: Session, monkeypatch: pytest.MonkeyPatch) -> project_send.Clients:
    """``tasks`` on this session, with tools a test can swap: Slack sent to."""

    @contextmanager
    def same_session() -> Iterator[Session]:
        yield session

    tools, _, _, _ = clients()
    project_send.send(session, MEETING, ["slack"], tools)
    monkeypatch.setattr(tasks, "session_scope", same_session)
    monkeypatch.setattr(tasks, "_project_clients", lambda *_: tools)
    return tools


def _owed(session: Session) -> ExtProjectRefreshOwed | None:
    session.flush()  # the shared session never commits; do not expire what it holds
    return session.get(ExtProjectRefreshOwed, MEETING)


def test_a_refresh_that_leaves_a_copy_behind_is_owed_and_retried_until_in_line(
    session: Session, in_tasks: project_send.Clients
) -> None:
    """Before: the failure was logged and the deleted sentence stayed outside
    for as long as the meeting was kept."""
    working = in_tasks.slack
    assert working is not None
    _withdraw(session, "dec_ok")
    in_tasks.slack = (SlackDownForEdits(), "C_TEAM")

    assert tasks.refresh_project_minutes(MEETING) is False
    assert _owed(session) is not None

    assert tasks.retry_project_minutes_refresh() == 0
    owed = _owed(session)
    assert owed is not None and owed.attempts == 1

    in_tasks.slack = working
    assert tasks.retry_project_minutes_refresh() == 1
    assert _owed(session) is None
    assert "배포는 금요일" not in working[0].updated[-1][2]


def test_a_tool_no_longer_connected_leaves_the_refresh_owed(
    session: Session, in_tasks: project_send.Clients
) -> None:
    _withdraw(session, "dec_ok")
    in_tasks.slack = None

    assert tasks.refresh_project_minutes(MEETING) is False
    assert _owed(session) is not None


def test_a_refresh_that_breaks_outright_is_owed_too(
    session: Session, in_tasks: project_send.Clients, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: Any) -> project_send.Clients:
        raise RuntimeError("could not even build the clients")

    monkeypatch.setattr(tasks, "_project_clients", broken)

    assert tasks.refresh_project_minutes(MEETING) is False
    assert _owed(session) is not None


def test_a_refresh_with_nothing_behind_owes_nothing_and_settles_what_was_owed(
    session: Session, in_tasks: project_send.Clients
) -> None:
    project_send.owe_refresh(session, [MEETING])
    project_send.owe_refresh(session, [MEETING])  # safe to repeat

    assert tasks.refresh_project_minutes(MEETING) is True
    assert _owed(session) is None


def test_an_owed_refresh_is_given_up_on_only_after_its_attempts(
    session: Session, in_tasks: project_send.Clients, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "PROJECT_REFRESH_MAX_ATTEMPTS", 3)
    _withdraw(session, "dec_ok")
    in_tasks.slack = None
    tasks.refresh_project_minutes(MEETING)

    tasks.retry_project_minutes_refresh()
    tasks.retry_project_minutes_refresh()
    owed = _owed(session)
    assert owed is not None and owed.attempts == 2

    tasks.retry_project_minutes_refresh()
    assert _owed(session) is None
    stale = session.get(ExtProjectSend, (MEETING, "prj_a", "slack"))
    assert stale is not None
    assert stale.content_digest != project_send._digest(
        project_send.minutes(session, MEETING)[0][0]
    )


def test_deleted_speech_owes_the_refresh_in_its_own_commit_and_queues_it(
    session: Session, in_tasks: project_send.Clients, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The person deleting their speech does not wait on the tools, and the
    rewrite is on record before anything is asked of a queue."""
    queued: list[str] = []
    monkeypatch.setattr(
        tasks.service,
        "forget_speech",
        lambda _session, _ids: service.SpeechForgotten(changed_items=("act_ok",)),
    )
    monkeypatch.setattr(
        tasks, "refresh_project_minutes_queued", SimpleNamespace(delay=queued.append)
    )
    monkeypatch.setattr(tasks, "sync_item_copies", SimpleNamespace(delay=lambda _id: None))
    working = in_tasks.slack
    assert working is not None

    tasks.forget_deleted_speech("user_1", ["utt_1"])

    assert queued == [MEETING]
    assert _owed(session) is not None
    assert working[0].updated == [], "nothing asked of Slack inside the deletion"


def test_deleted_speech_is_still_owed_when_nothing_can_be_queued(
    session: Session, in_tasks: project_send.Clients, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_broker(_meeting_id: str) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(
        tasks.service,
        "forget_speech",
        lambda _session, _ids: service.SpeechForgotten(changed_decisions=("dec_ok",)),
    )
    monkeypatch.setattr(tasks, "refresh_project_minutes_queued", SimpleNamespace(delay=no_broker))

    tasks.forget_deleted_speech("user_1", ["utt_1"])  # does not raise

    assert _owed(session) is not None


def test_every_extraction_ends_with_one_refresh_of_its_meeting(
    session: Session, in_tasks: project_send.Clients, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confirmed decision the rebuild no longer has is named by no
    correction; the run itself has to bring the minutes in line."""
    refreshed: list[str] = []
    monkeypatch.setattr(tasks, "refresh_project_minutes", refreshed.append)
    monkeypatch.setattr(tasks, "get_classifier", FakeClassifier)
    monkeypatch.setattr(tasks, "get_nli", FakeNli)
    monkeypatch.setattr(tasks, "publish", lambda *_args, **_kwargs: None)
    for task in ("sync_item_copies", "sync_decision", "update_confirmation_dm"):
        monkeypatch.setattr(tasks, task, SimpleNamespace(delay=lambda _id: None))

    tasks._extract(MEETING, [])

    assert refreshed == [MEETING]


# --- every change that takes a row out of the minutes refreshes them (#787 review) ---


@pytest.fixture
def api(session: Session, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, list[str]]:
    """The router on this session, and the meetings a refresh was asked for.
    The two sync tasks are recorded apart: they end with a refresh of their own."""
    refreshed: list[str] = []
    monkeypatch.setattr(tasks, "refresh_project_minutes", refreshed.append)
    monkeypatch.setattr(
        tasks, "sync_decision_after_confirmation", lambda _id: refreshed.append("via decision sync")
    )
    monkeypatch.setattr(
        tasks, "sync_after_confirmation", lambda _id: refreshed.append("via item sync")
    )
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session, team_id=TEAM)
    return TestClient(app), refreshed


def test_taking_a_confirmation_back_refreshes_the_minutes_with_no_notion_page(
    api: tuple[TestClient, list[str]],
) -> None:
    """A team that sent its minutes to Slack or Jira alone has no decision
    page, and the withdrawn decision stayed in the copy."""
    client, refreshed = api

    answer = client.patch(f"{PREFIX}/decisions/dec_ok", json={"status": "pending"})

    assert answer.status_code == 200
    assert refreshed == [MEETING]


def test_deleting_a_decision_a_person_added_refreshes_its_meetings_minutes(
    session: Session, api: tuple[TestClient, list[str]]
) -> None:
    """The row is really deleted, so the meeting has to be read before."""
    session.add(
        ExtDecision(
            id="dec_typed",
            meeting_id=MEETING,
            statement="사람이 적은 결정",
            confidence=1.0,
            origin="user",
            project_id="prj_a",
        )
    )
    session.flush()
    client, refreshed = api

    answer = client.delete(f"{PREFIX}/decisions/dec_typed")

    assert answer.status_code == 204
    assert session.get(ExtDecision, "dec_typed") is None
    assert refreshed == [MEETING]


def test_moving_an_item_back_to_needs_confirmation_refreshes_the_minutes(
    api: tuple[TestClient, list[str]],
) -> None:
    """Its only copy outside is the minutes, so nothing of its own follows --
    and an unconfirmed item stayed in them (#246)."""
    client, refreshed = api

    answer = client.patch(f"{PREFIX}/action-items/act_ok", json={"status": "needs_confirmation"})

    assert answer.status_code == 200
    assert refreshed == [MEETING]


# --- the same for the events on people's own calendars (#788 review) ----------------


def test_an_event_whose_owner_cannot_be_reached_is_owed_and_retried(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody else can touch a person's calendar: an event left behind there
    has the retry and nothing else."""
    calendar = FakeCalendar()
    project_send.send(session, MEETING, ["calendar"], calendar_clients(calendar))
    _withdraw(session, "dec_ok")
    reachable: list[bool] = []

    @contextmanager
    def same_session() -> Iterator[Session]:
        yield session

    @contextmanager
    def calendars(_session: Session) -> Iterator[Any]:
        yield lambda _user_id: (calendar, "primary") if reachable else None

    monkeypatch.setattr(tasks, "session_scope", same_session)
    monkeypatch.setattr(tasks, "_project_clients", lambda *_: project_send.Clients())
    monkeypatch.setattr(tasks, "_calendars", calendars)

    assert tasks.refresh_project_minutes(MEETING) is False
    assert _owed(session) is not None
    assert calendar.updated == []

    reachable.append(True)
    assert tasks.retry_project_minutes_refresh() == 1
    assert _owed(session) is None
    assert calendar.updated == ["evt_1"], "Autune's event rewritten; App's was unchanged"


def test_deleted_speech_owes_a_meeting_that_went_to_calendars_only(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_send.send(session, MEETING, ["calendar"], calendar_clients(FakeCalendar()))
    queued: list[str] = []

    @contextmanager
    def same_session() -> Iterator[Session]:
        yield session

    monkeypatch.setattr(tasks, "session_scope", same_session)
    monkeypatch.setattr(
        tasks.service,
        "forget_speech",
        lambda _session, _ids: service.SpeechForgotten(changed_decisions=("dec_ok",)),
    )
    monkeypatch.setattr(
        tasks, "refresh_project_minutes_queued", SimpleNamespace(delay=queued.append)
    )
    monkeypatch.setattr(tasks, "sync_decision", SimpleNamespace(delay=lambda _id: None))

    tasks.forget_deleted_speech("user_1", ["utt_1"])

    assert queued == [MEETING]
    assert _owed(session) is not None


def test_a_row_with_a_short_title_is_listed_by_it(session: Session) -> None:
    """Module B's owner, 2026-10-09: "Slack·회의록까지 전부". A line of the
    minutes is the title alone, and takes no kind mark: it stands under a
    heading that says the kind."""
    row = session.get(ExtActionItem, "act_ok")
    decision = session.get(ExtDecision, "dec_ok")
    assert row is not None and decision is not None
    row.title, decision.title = "릴리스 노트", "배포 금요일"
    session.flush()

    found, _ = project_send.minutes(session, MEETING)

    autune = found[0]
    assert autune.decisions == ("배포 금요일",)
    assert autune.items == ("릴리스 노트 (담당 민경, 기한 2026-10-03)",)
    assert "릴리스 노트 정리" not in autune.text and "배포는 금요일로 한다" not in autune.text
    assert "[할 일]" not in autune.text and "[결정]" not in autune.text
