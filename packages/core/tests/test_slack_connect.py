"""One-click Slack install (#428) -- without a network, a Redis or a Postgres."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from autune_core import SESSION_COOKIE, issue_token
from autune_core import auth_router as auth_router_module
from autune_core.auth_router import router as auth_router
from autune_core.db import Base, get_session
from autune_core.entities import Meeting, Team, TeamMember, User
from autune_core.errors import AutuneError, PermissionDeniedError
from autune_core.integrations_config import IntegrationConfig
from autune_core.oauth.slack import (
    BOT_SCOPES,
    SlackChannel,
    SlackInstall,
    SlackOAuthClient,
    get_slack_oauth_client,
)
from autune_core.oauth.state import InMemoryStateStore, get_state_store

ME, OUTSIDER, TEAM, MEETING = "user_me", "user_out", "team_1", "mtg_1"


class FakeSlack:
    def __init__(self) -> None:
        self.install: SlackInstall | Exception = SlackInstall("xoxb-1", "U_BOT", "T1", "Acme")
        self.channel = SlackChannel("C1", "autune", created=True)
        self.revoked: list[str] = []

    def authorization_url(self, *, state: str) -> str:
        return f"https://slack.com/oauth/v2/authorize?state={state}"

    def exchange_code(self, code: str) -> SlackInstall:
        if isinstance(self.install, Exception):
            raise self.install
        return self.install

    def ensure_channel(self, token: str, name: str) -> SlackChannel:
        return self.channel

    def revoke(self, token: str) -> bool:
        self.revoked.append(token)
        return True


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
        saved[team_id] = {k: v for k, v in kw.items() if v is not None}

    def load(_s: Any, team_id: str, service: str) -> IntegrationConfig | None:
        row = saved.get(team_id)
        return (
            None
            if row is None
            else IntegrationConfig(service, team_id, row["secret"], row["config"])
        )

    monkeypatch.setattr(auth_router_module, "save_integration", save)
    monkeypatch.setattr(auth_router_module, "load_integration", load)
    monkeypatch.setattr(
        auth_router_module, "disconnect_integration", lambda _s, t, _svc: saved.pop(t)
    )
    store = InMemoryStateStore()
    slack = FakeSlack()
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")

    @app.exception_handler(AutuneError)
    def _handle(_req: object, exc: AutuneError):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_state_store] = lambda: store
    app.dependency_overrides[get_slack_oauth_client] = lambda: slack
    return {"app": app, "store": store, "slack": slack, "saved": saved}


def signed_in(world: dict[str, Any], user_id: str = ME) -> TestClient:
    client = TestClient(world["app"], follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, issue_token(user_id))
    return client


def start(client: TestClient) -> str:
    response = client.get(
        f"/api/auth/slack/start?meeting_id={MEETING}&redirect_to=/meetings/{MEETING}/actions"
    )
    assert response.status_code == 307, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def test_only_a_member_of_the_meetings_team_can_start(world: dict[str, Any]) -> None:
    response = signed_in(world, OUTSIDER).get(f"/api/auth/slack/start?meeting_id={MEETING}")
    assert response.status_code == 403
    assert world["store"]._entries == {}


def test_installing_stores_the_bot_token_and_the_alert_channel(world: dict[str, Any]) -> None:
    client = signed_in(world)
    response = client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert response.headers["location"].endswith(f"/meetings/{MEETING}/actions?slack=connected")
    row = world["saved"][TEAM]
    assert row["secret"] == "xoxb-1"
    assert row["connected_by"] == ME
    # ``channel`` is the key D's briefing and E's report already read.
    assert row["config"]["channel"] == "C1"
    assert row["config"]["channel_name"] == "autune"
    assert row["config"]["workspace_name"] == "Acme"


@pytest.mark.parametrize("case", ["declined", "refused"])
def test_a_failed_install_goes_back_to_the_screen(world: dict[str, Any], case: str) -> None:
    client = signed_in(world)
    state = start(client)
    if case == "refused":
        world["slack"].install = PermissionDeniedError("Slack rejected the authorization code")
    extra = "&error=access_denied" if case == "declined" else "&code=c"

    response = client.get(f"/api/auth/slack/callback?state={state}{extra}")

    assert "?slack=failed&reason=" in response.headers["location"]
    assert world["saved"] == {}


def test_status_and_disconnect_revoke_the_token(world: dict[str, Any]) -> None:
    client = signed_in(world)
    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert client.get(f"/api/auth/slack?meeting_id={MEETING}").json() == {
        "connected": True,
        "workspace_name": "Acme",
        "channel_name": "autune",
    }
    body = client.post(f"/api/auth/slack/disconnect?meeting_id={MEETING}").json()

    assert body == {"connected": False, "revoked": True}
    assert world["slack"].revoked == ["xoxb-1"]
    assert world["saved"] == {}


# --- the client at its own door --------------------------------------------------------


def _client(answers: dict[str, dict[str, Any]], calls: list[str]) -> SlackOAuthClient:
    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append(method)
        return httpx.Response(200, json=answers[method])

    return SlackOAuthClient(
        client_id="cid",
        client_secret="cs",
        redirect_uri="https://example.test/api/auth/slack/callback",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_the_install_url_asks_for_bot_scopes_only() -> None:
    url = _client({}, []).authorization_url(state="s")
    query = parse_qs(urlsplit(url).query)
    assert query["scope"] == [",".join(BOT_SCOPES)]
    assert "user_scope" not in query
    assert "users:read.email" not in query["scope"][0]


def test_a_new_channel_is_made() -> None:
    calls: list[str] = []
    client = _client(
        {"conversations.create": {"ok": True, "channel": {"id": "C9", "name": "autune"}}}, calls
    )
    assert client.ensure_channel("xoxb", "autune") == SlackChannel("C9", "autune", created=True)
    assert calls == ["conversations.create"]


def test_an_existing_channel_of_that_name_is_joined() -> None:
    calls: list[str] = []
    client = _client(
        {
            "conversations.create": {"ok": False, "error": "name_taken"},
            "conversations.list": {"ok": True, "channels": [{"id": "C7", "name": "autune"}]},
            "conversations.join": {"ok": True},
        },
        calls,
    )
    assert client.ensure_channel("xoxb", "autune") == SlackChannel("C7", "autune", created=False)
    assert calls == ["conversations.create", "conversations.list", "conversations.join"]


def test_a_refused_code_says_why_without_the_code() -> None:
    client = _client({"oauth.v2.access": {"ok": False, "error": "invalid_code"}}, [])
    with pytest.raises(PermissionDeniedError) as caught:
        client.exchange_code("the-secret-code")
    assert "invalid_code" in str(caught.value)
    assert "the-secret-code" not in str(caught.value)


def test_a_private_autune_falls_back_to_autune_alerts() -> None:
    """Found on a real workspace: someone had made a private #autune."""
    calls: list[str] = []
    answers = iter(
        [
            {"ok": False, "error": "name_taken"},  # create #autune
            {"ok": True, "channels": []},  # list: not public
            {"ok": True, "channel": {"id": "C5", "name": "autune-alerts"}},  # create #autune-alerts
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=next(answers))

    client = SlackOAuthClient(
        client_id="cid",
        client_secret="cs",
        redirect_uri="https://example.test/cb",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.ensure_channel("xoxb", "autune") == SlackChannel(
        "C5", "autune-alerts", created=True
    )
    assert calls == ["conversations.create", "conversations.list", "conversations.create"]


def test_no_usable_channel_is_a_named_failure(world: dict[str, Any]) -> None:
    from autune_core.oauth.slack import SlackChannelUnavailableError

    class NoChannel(FakeSlack):
        def ensure_channel(self, token: str, name: str) -> SlackChannel:
            raise SlackChannelUnavailableError("both taken")

    world["app"].dependency_overrides[get_slack_oauth_client] = lambda: NoChannel()
    client = signed_in(world)
    response = client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert response.headers["location"].endswith("?slack=failed&reason=slack_channel_unavailable")
    assert world["saved"] == {}
