"""One-click Jira connect (#82, #428) and the rotating-token helper -- without a
network, a Redis or a Postgres."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from autune_core import SESSION_COOKIE, issue_token, jira_connection
from autune_core import auth_router as auth_router_module
from autune_core.auth_router import router as auth_router
from autune_core.db import Base, get_session
from autune_core.entities import Meeting, Team, TeamMember, User
from autune_core.errors import AutuneError
from autune_core.integrations_config import IntegrationConfig
from autune_core.oauth.atlassian import (
    AtlassianOAuthClient,
    AtlassianTokens,
    JiraProject,
    JiraReconnectRequiredError,
    JiraSite,
    get_atlassian_client,
)
from autune_core.oauth.state import InMemoryStateStore, get_state_store

ME, OUTSIDER, TEAM, MEETING = "user_me", "user_out", "team_1", "mtg_1"


class FakeAtlassian:
    def __init__(self) -> None:
        self.tokens = AtlassianTokens("access", "refresh-1", frozenset({"offline_access"}))
        self.site_list = [JiraSite("cloud-1", "https://acme.atlassian.net", "acme")]
        self.project_list = [JiraProject("AUT", "Autune")]

    def authorization_url(self, *, state: str) -> str:
        return f"https://auth.atlassian.com/authorize?state={state}"

    def exchange_code(self, code: str) -> AtlassianTokens:
        return self.tokens

    def sites(self, access_token: str) -> list[JiraSite]:
        return self.site_list

    def projects(self, access_token: str, cloud_id: str) -> list[JiraProject]:
        return self.project_list


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, Team.__table__, TeamMember.__table__, Meeting.__table__],
    )
    db = sessionmaker(bind=engine)()
    db.add(Team(id=TEAM, name="t"))
    for uid in (ME, OUTSIDER):
        db.add(User(id=uid, email=f"{uid}@example.com", display_name=uid))
    db.flush()
    db.add(TeamMember(team_id=TEAM, user_id=ME))
    db.add(Meeting(id=MEETING, team_id=TEAM, title="m"))
    db.commit()

    saved: dict[str, dict[str, Any]] = {}

    def save(_s: Any, team_id: str, service: str, **kw: Any) -> None:
        row = saved.setdefault(team_id, {"config": {}})
        row.update({k: v for k, v in kw.items() if v is not None})

    def load(_s: Any, team_id: str, service: str) -> IntegrationConfig | None:
        row = saved.get(team_id)
        if row is None:
            return None
        return IntegrationConfig(service, team_id, row.get("secret"), dict(row["config"]))

    monkeypatch.setattr(auth_router_module, "save_integration", save)
    monkeypatch.setattr(auth_router_module, "load_integration", load)
    monkeypatch.setattr(
        auth_router_module, "disconnect_integration", lambda _s, t, _svc: saved.pop(t)
    )

    store = InMemoryStateStore()
    atlassian = FakeAtlassian()
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")

    @app.exception_handler(AutuneError)
    def _handle(_req: object, exc: AutuneError):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_state_store] = lambda: store
    app.dependency_overrides[get_atlassian_client] = lambda: atlassian
    return {"app": app, "store": store, "atlassian": atlassian, "saved": saved}


def signed_in(world: dict[str, Any], user_id: str = ME) -> TestClient:
    client = TestClient(world["app"], follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, issue_token(user_id))
    return client


def start(client: TestClient) -> str:
    response = client.get(
        f"/api/auth/jira/start?meeting_id={MEETING}&redirect_to=/meetings/{MEETING}/actions"
    )
    assert response.status_code == 307, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def finish(client: TestClient, state: str, extra: str = "&code=the-code") -> httpx.Response:
    return client.get(f"/api/auth/jira/callback?state={state}{extra}")


def test_only_a_member_of_the_meetings_team_can_start(world: dict[str, Any]) -> None:
    response = signed_in(world, OUTSIDER).get(f"/api/auth/jira/start?meeting_id={MEETING}")
    assert response.status_code == 403
    assert world["store"]._entries == {}


def test_connecting_stores_the_teams_grant_site_and_only_project(world: dict[str, Any]) -> None:
    client = signed_in(world)
    response = finish(client, start(client))

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/meetings/{MEETING}/actions?jira=connected")
    row = world["saved"][TEAM]
    assert row["secret"] == "refresh-1"
    assert row["connected_by"] == ME
    assert row["config"] == {
        "cloud_id": "cloud-1",
        "site_url": "https://acme.atlassian.net",
        "site_name": "acme",
        "project_key": "AUT",
        "needs_reconnect": False,
    }


def test_several_projects_are_left_for_the_screen_to_choose(world: dict[str, Any]) -> None:
    world["atlassian"].project_list = [JiraProject("A", "a"), JiraProject("B", "b")]
    client = signed_in(world)
    finish(client, start(client))
    assert world["saved"][TEAM]["config"]["project_key"] is None


@pytest.mark.parametrize(
    "case",
    ["declined", "no_offline_access", "no_site"],
)
def test_a_failed_connect_goes_back_to_the_screen_and_stores_nothing(
    world: dict[str, Any], case: str
) -> None:
    client = signed_in(world)
    state = start(client)
    extra = "&code=the-code"
    if case == "declined":
        extra = "&error=access_denied"
    elif case == "no_offline_access":
        world["atlassian"].tokens = AtlassianTokens("access", None, frozenset())
    else:
        world["atlassian"].site_list = []

    response = finish(client, state, extra)

    assert response.headers["location"].endswith("?jira=failed")
    assert world["saved"] == {}


def test_a_callback_from_another_browser_stores_nothing(world: dict[str, Any]) -> None:
    state = start(signed_in(world))
    assert finish(signed_in(world), state).status_code == 403  # no state cookie
    assert world["saved"] == {}


def test_status_and_disconnect_are_for_members_only(world: dict[str, Any]) -> None:
    client = signed_in(world)
    finish(client, start(client))

    status = client.get(f"/api/auth/jira?meeting_id={MEETING}").json()
    assert status == {
        "connected": True,
        "needs_reconnect": False,
        "site_name": "acme",
        "project_key": "AUT",
    }
    assert signed_in(world, OUTSIDER).get(f"/api/auth/jira?meeting_id={MEETING}").status_code == 403

    body = client.post(f"/api/auth/jira/disconnect?meeting_id={MEETING}").json()
    assert body == {"connected": False, "revoked": False}
    assert world["saved"] == {}


# --- jira_access: the rotating refresh token ----------------------------------------


class _Row:
    def __init__(self, secret: str | None, config: dict[str, Any]) -> None:
        self.secret = secret
        self.config = config


class _Session:
    def __init__(self, row: _Row | None) -> None:
        self.row = row
        self.commits = 0

    def scalars(self, _stmt: Any) -> _Session:
        return self

    def one_or_none(self) -> _Row | None:
        return self.row

    def commit(self) -> None:
        self.commits += 1


def _patch_scope(monkeypatch: pytest.MonkeyPatch, session: _Session) -> None:
    @contextmanager
    def scope() -> Iterator[_Session]:
        yield session

    monkeypatch.setattr(jira_connection, "session_scope", scope)
    monkeypatch.setattr(jira_connection, "encrypt", lambda v: f"enc:{v}")
    monkeypatch.setattr(jira_connection, "decrypt", lambda v: v.removeprefix("enc:"))


class _Refresher:
    def __init__(self, answer: AtlassianTokens | Exception) -> None:
        self.answer = answer
        self.seen: list[str] = []

    def refresh(self, token: str) -> AtlassianTokens:
        self.seen.append(token)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_access_rotates_and_keeps_the_new_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _Row("enc:old", {"cloud_id": "cloud-1", "project_key": "AUT"})
    _patch_scope(monkeypatch, _Session(row))
    refresher = _Refresher(AtlassianTokens("access-2", "new", frozenset()))

    access = jira_connection.jira_access(TEAM, client=refresher)  # type: ignore[arg-type]

    assert refresher.seen == ["old"]
    assert row.secret == "enc:new"
    assert access is not None
    assert (access.access_token, access.cloud_id, access.project_key) == (
        "access-2",
        "cloud-1",
        "AUT",
    )
    assert "access-2" not in repr(access)


def test_a_refused_token_marks_the_team_for_reconnect_once(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _Row("enc:old", {"cloud_id": "cloud-1"})
    session = _Session(row)
    _patch_scope(monkeypatch, session)
    refresher = _Refresher(JiraReconnectRequiredError("refused"))

    with pytest.raises(JiraReconnectRequiredError):
        jira_connection.jira_access(TEAM, client=refresher)  # type: ignore[arg-type]
    assert row.config["needs_reconnect"] is True
    assert session.commits == 1

    assert jira_connection.jira_access(TEAM, client=refresher) is None  # type: ignore[arg-type]
    assert len(refresher.seen) == 1  # not retried on every edit


def test_no_connection_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_scope(monkeypatch, _Session(None))
    assert jira_connection.jira_access(TEAM, client=_Refresher(AssertionError())) is None  # type: ignore[arg-type]


# --- the client at its own door --------------------------------------------------------


def _client(handler: Any) -> AtlassianOAuthClient:
    return AtlassianOAuthClient(
        client_id="cid",
        client_secret="cs",
        redirect_uri="http://localhost:3000/api/auth/jira/callback",
        scopes="read:jira-work write:jira-work read:jira-user offline_access",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_the_consent_url_asks_the_api_audience_for_offline_access() -> None:
    query = parse_qs(urlsplit(_client(lambda r: None).authorization_url(state="s")).query)
    assert query["audience"] == ["api.atlassian.com"]
    assert "offline_access" in query["scope"][0]
    assert query["prompt"] == ["consent"]


def test_a_refused_refresh_is_reconnect_and_says_nothing_secret() -> None:
    client = _client(lambda r: httpx.Response(403, json={"error": "invalid_grant"}))
    with pytest.raises(JiraReconnectRequiredError) as caught:
        client.refresh("the-refresh-token")
    assert "the-refresh-token" not in str(caught.value)
