"""GET /api/extraction/jira/issues: a team's open Jira issues, read to be shown
and never stored (decided with the user, 2026-10-02).

SQLite in memory and the router on a bare app, as ``test_read_endpoints`` does.
Jira itself is a stub: what is under test is who may read which team's project,
what the answer says when a connection cannot answer, and that nothing of the
answer is written or logged.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import (
    AutuneError,
    Base,
    IntegrationConfig,
    JiraAccess,
    Meeting,
    Team,
    TeamMember,
    User,
    get_session,
)
from autune_core.errors import PrivacyViolationError
from autune_core.oauth.atlassian import JiraReconnectRequiredError
from autune_extraction import jira_issues
from autune_extraction.models import ExtActionItem, ExtExternalRef
from autune_extraction.router import router
from autune_integrations import TransientIntegrationError
from autune_integrations.jira import JiraIssue

from .conftest import sign_in

PREFIX = "/api/extraction"
MINE, THEIRS, BARE = "team_1", "team_other", "team_bare"
SITE = "https://acme.atlassian.net"
SECRET_TITLE = "고객사 계약 조건 검토"

TABLES = [
    Meeting.__table__,
    User.__table__,
    Team.__table__,
    TeamMember.__table__,
    ExtActionItem.__table__,
    ExtExternalRef.__table__,
]


class StubJira:
    """Stands where ``JiraClient.for_cloud`` returns a client. Records which
    project was asked for."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.asked: list[str] = []
        self.closed = 0

    def open_issues(self, project_key: str) -> tuple[list[JiraIssue], bool]:
        self.asked.append(project_key)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def close(self) -> None:
        self.closed += 1


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        for team_id, name in ((MINE, "제품팀"), (THEIRS, "남의 팀"), (BARE, "연결 없는 팀")):
            session.add(Team(id=team_id, name=name))
        session.add(Meeting(id="mtg_1", team_id=MINE, title="주간 회의"))
        session.add(Meeting(id="mtg_theirs", team_id=THEIRS, title="남의 회의"))
        # Somebody is on the other team. Without a member there, "only the
        # caller's teams" would hold for any query over ``team_members``.
        session.add(TeamMember(team_id=THEIRS, user_id="user_stranger"))
        session.flush()
        yield session


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session, team_id=MINE)
    yield TestClient(app)


@pytest.fixture
def jira(monkeypatch: pytest.MonkeyPatch) -> StubJira:
    """``MINE`` and ``THEIRS`` have a Jira project; ``BARE`` never connected."""
    stub = StubJira(
        (
            [
                JiraIssue(
                    "AUT-7", SECRET_TITLE, "진행 중", "indeterminate", "가나다", date(2026, 10, 9)
                ),
                JiraIssue("AUT-8", "미정", "Backlog", "new", None, None),
            ],
            False,
        )
    )
    configs: dict[str, dict[str, Any]] = {
        MINE: {"site_url": SITE, "project_key": "AUT", "cloud_id": "cloud-1"},
        THEIRS: {"site_url": SITE, "project_key": "SEC", "cloud_id": "cloud-2"},
    }
    stub.configs = configs  # type: ignore[attr-defined]
    stub.access_for = []  # type: ignore[attr-defined]

    def load(_session: Session, team_id: str, _service: str) -> IntegrationConfig | None:
        found = configs.get(team_id)
        return None if found is None else IntegrationConfig("jira", team_id, "r", found)

    def access(team_id: str, **_: Any) -> JiraAccess | None:
        stub.access_for.append(team_id)  # type: ignore[attr-defined]
        found = configs[team_id]
        return JiraAccess("acc-token", found["cloud_id"], found.get("project_key"))

    monkeypatch.setattr(jira_issues, "load_integration", load)
    monkeypatch.setattr(jira_issues, "jira_access", access)
    monkeypatch.setattr(jira_issues.JiraClient, "for_cloud", staticmethod(lambda _t, _c: stub))
    return stub


def test_a_member_sees_their_teams_open_issues_with_a_link_each(
    client: TestClient, jira: StubJira
) -> None:
    body = client.get(f"{PREFIX}/jira/issues").json()

    assert body == [
        {
            "team_id": MINE,
            "team_name": "제품팀",
            "project_key": "AUT",
            "state": "ok",
            "more": False,
            "issues": [
                {
                    "key": "AUT-7",
                    "summary": SECRET_TITLE,
                    "status": "진행 중",
                    "status_category": "indeterminate",
                    "assignee": "가나다",
                    "due_date": "2026-10-09",
                    "url": f"{SITE}/browse/AUT-7",
                    "from_autune": False,
                },
                {
                    "key": "AUT-8",
                    "summary": "미정",
                    "status": "Backlog",
                    "status_category": "new",
                    "assignee": None,
                    "due_date": None,
                    "url": f"{SITE}/browse/AUT-8",
                    "from_autune": False,
                },
            ],
        }
    ]
    assert jira.asked == ["AUT"]
    assert jira.closed == 1


def test_only_the_callers_own_teams_are_ever_asked_about(
    client: TestClient, jira: StubJira
) -> None:
    """``THEIRS`` has a project too. Without a team named, the answer covers the
    caller's teams -- and no token is fetched for anyone else's."""
    body = client.get(f"{PREFIX}/jira/issues").json()

    assert [project["team_id"] for project in body] == [MINE]
    assert jira.access_for == [MINE]  # type: ignore[attr-defined]
    assert jira.asked == ["AUT"]


def test_naming_another_teams_project_is_the_404_an_unknown_team_gets(
    client: TestClient, jira: StubJira
) -> None:
    assert client.get(f"{PREFIX}/jira/issues", params={"team_id": THEIRS}).status_code == 404
    assert client.get(f"{PREFIX}/jira/issues", params={"team_id": "team_nope"}).status_code == 404
    assert (
        client.get(f"{PREFIX}/jira/issues", params={"meeting_id": "mtg_theirs"}).status_code == 404
    )
    assert jira.access_for == []  # type: ignore[attr-defined]
    assert jira.asked == []


def test_a_meeting_names_its_team(client: TestClient, jira: StubJira) -> None:
    body = client.get(f"{PREFIX}/jira/issues", params={"meeting_id": "mtg_1"}).json()

    assert [project["team_id"] for project in body] == [MINE]


def test_a_team_that_never_connected_jira_is_left_out(
    client: TestClient, session: Session, jira: StubJira
) -> None:
    session.add(TeamMember(team_id=BARE, user_id="user_reader"))
    session.flush()

    body = client.get(f"{PREFIX}/jira/issues").json()

    assert [project["team_id"] for project in body] == [MINE]
    assert jira.access_for == [MINE]  # type: ignore[attr-defined]


def test_nothing_of_the_answer_is_written_or_logged(
    client: TestClient, session: Session, jira: StubJira
) -> None:
    """Viewed, not imported: no row anywhere, and no log line carrying a title
    or a person's name."""
    with capture_logs() as logs:
        assert client.get(f"{PREFIX}/jira/issues").status_code == 200

    for table in TABLES:
        if table.name in {"teams", "team_members", "meetings"}:
            continue
        assert session.scalar(select(func.count()).select_from(table)) == 0, table.name
    assert not session.new and not session.dirty
    written = repr(logs)
    assert SECRET_TITLE not in written
    assert "가나다" not in written
    assert "AUT-7" not in written


def test_an_issue_autune_made_is_marked_and_another_sites_same_key_is_not(
    client: TestClient, session: Session, jira: StubJira
) -> None:
    for item_id, key, site in (("act_1", "AUT-7", "cloud-1"), ("act_2", "AUT-8", "cloud-9")):
        session.add(
            ExtActionItem(
                id=item_id,
                meeting_id="mtg_1",
                description="할 일",
                status="todo",
                confidence=1.0,
                origin="user",
            )
        )
        session.add(
            ExtExternalRef(
                action_item_id=item_id,
                system="jira",
                meeting_id="mtg_1",
                external_id=key,
                site=site,
            )
        )
    session.flush()

    issues = client.get(f"{PREFIX}/jira/issues").json()[0]["issues"]

    assert {issue["key"]: issue["from_autune"] for issue in issues} == {
        "AUT-7": True,
        "AUT-8": False,
    }


def test_no_project_chosen_says_so_and_asks_jira_nothing(
    client: TestClient, jira: StubJira
) -> None:
    jira.configs[MINE]["project_key"] = None  # type: ignore[attr-defined]

    body = client.get(f"{PREFIX}/jira/issues").json()

    assert [(p["state"], p["issues"]) for p in body] == [("no_project", [])]
    assert jira.access_for == []  # type: ignore[attr-defined]
    assert jira.asked == []


def test_a_connection_that_needs_reconnecting_says_so(
    client: TestClient, jira: StubJira, monkeypatch: pytest.MonkeyPatch
) -> None:
    jira.configs[MINE]["needs_reconnect"] = True  # type: ignore[attr-defined]
    assert client.get(f"{PREFIX}/jira/issues").json()[0]["state"] == "needs_reconnect"
    assert jira.access_for == []  # type: ignore[attr-defined]

    # And the request that finds the grant refused, before the flag is set.
    jira.configs[MINE]["needs_reconnect"] = False  # type: ignore[attr-defined]

    def refused(_team: str, **_: Any) -> None:
        raise JiraReconnectRequiredError("refused")

    monkeypatch.setattr(jira_issues, "jira_access", refused)
    assert client.get(f"{PREFIX}/jira/issues").json()[0]["state"] == "needs_reconnect"
    assert jira.asked == []


def test_a_jira_that_does_not_answer_is_unavailable_not_an_error(
    client: TestClient, jira: StubJira
) -> None:
    jira.answer = TransientIntegrationError("jira timed out")

    response = client.get(f"{PREFIX}/jira/issues")

    assert response.status_code == 200
    assert [(p["state"], p["issues"]) for p in response.json()] == [("unavailable", [])]
    assert jira.closed == 1


def test_a_token_that_cannot_be_fetched_is_unavailable_not_a_500(
    client: TestClient, jira: StubJira, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atlassian's token endpoint down is a plain ``AutuneError`` from
    ``jira_access``, not an integration error. It used to rise as a 500 with
    Atlassian's words in the body (reproduced in review of #738)."""

    def down(_team: str, **_: Any) -> None:
        raise AutuneError("Atlassian answered 503")

    monkeypatch.setattr(jira_issues, "jira_access", down)

    response = client.get(f"{PREFIX}/jira/issues")

    assert response.status_code == 200
    assert [(p["state"], p["issues"]) for p in response.json()] == [("unavailable", [])]
    assert "Atlassian" not in response.text
    assert jira.asked == []


def test_one_teams_token_failure_does_not_hide_another_teams_issues(
    client: TestClient, session: Session, jira: StubJira, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller is on both teams. ``THEIRS`` cannot get a token; ``MINE``
    still answers."""
    session.add(TeamMember(team_id=THEIRS, user_id="user_reader"))
    session.flush()
    working = jira_issues.jira_access

    def flaky(team_id: str, **kw: Any) -> JiraAccess | None:
        if team_id == THEIRS:
            raise AutuneError("Atlassian answered 503")
        return working(team_id, **kw)

    monkeypatch.setattr(jira_issues, "jira_access", flaky)

    response = client.get(f"{PREFIX}/jira/issues")

    assert response.status_code == 200
    by_team = {p["team_id"]: p for p in response.json()}
    assert by_team[THEIRS]["state"] == "unavailable"
    assert by_team[MINE]["state"] == "ok"
    assert [issue["key"] for issue in by_team[MINE]["issues"]] == ["AUT-7", "AUT-8"]


def test_a_privacy_violation_is_never_answered_around(
    client: TestClient, jira: StubJira, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``PrivacyViolationError`` is an ``AutuneError``. The clause that turns a
    token failure into ``unavailable`` must not take it with the rest."""

    def blocked(_team: str, **_: Any) -> None:
        raise PrivacyViolationError("blocked")

    monkeypatch.setattr(jira_issues, "jira_access", blocked)

    response = client.get(f"{PREFIX}/jira/issues")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "privacy_violation"


def test_a_link_is_only_built_on_https_and_a_key_that_is_one(
    client: TestClient, jira: StubJira
) -> None:
    jira.answer = ([JiraIssue("../admin", "x", None, None, None, None)], False)
    assert client.get(f"{PREFIX}/jira/issues").json()[0]["issues"][0]["url"] is None

    jira.answer = ([JiraIssue("AUT-1", "x", None, None, None, None)], False)
    jira.configs[MINE]["site_url"] = "javascript:alert(1)"  # type: ignore[attr-defined]
    assert client.get(f"{PREFIX}/jira/issues").json()[0]["issues"][0]["url"] is None


def test_it_says_when_jira_has_more(client: TestClient, jira: StubJira) -> None:
    jira.answer = ([JiraIssue("AUT-1", "x", None, None, None, None)], True)

    assert client.get(f"{PREFIX}/jira/issues").json()[0]["more"] is True
