"""One-click Notion connect (#428) -- without a network, a Redis or a Postgres."""

from __future__ import annotations

import json
from base64 import b64decode
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
from autune_core.oauth.notion import NotionGrant, NotionOAuthClient, get_notion_oauth_client
from autune_core.oauth.state import InMemoryStateStore, get_state_store

ME, OUTSIDER, TEAM, MEETING = "user_me", "user_out", "team_1", "mtg_1"


class FakeNotion:
    def __init__(self) -> None:
        self.answer: NotionGrant | Exception = NotionGrant("ntn_token", "ws-1", "Acme", "bot-1")
        self.exchanged = 0

    def authorization_url(self, *, state: str) -> str:
        return f"https://api.notion.com/v1/oauth/authorize?state={state}"

    def exchange_code(self, code: str) -> NotionGrant:
        self.exchanged += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


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
    # This test stores no encrypted secret; the check is its own test.
    monkeypatch.setattr(auth_router_module, "ensure_configured", lambda: None)
    monkeypatch.setattr(
        auth_router_module, "disconnect_integration", lambda _s, t, _svc: saved.pop(t)
    )
    store = InMemoryStateStore()
    notion = FakeNotion()
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")

    @app.exception_handler(AutuneError)
    def _handle(_req: object, exc: AutuneError):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_state_store] = lambda: store
    app.dependency_overrides[get_notion_oauth_client] = lambda: notion
    return {"app": app, "store": store, "notion": notion, "saved": saved}


def signed_in(world: dict[str, Any], user_id: str = ME) -> TestClient:
    client = TestClient(world["app"], follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, issue_token(user_id))
    return client


def start(client: TestClient) -> str:
    response = client.get(
        f"/api/auth/notion/start?meeting_id={MEETING}&redirect_to=/meetings/{MEETING}/actions"
    )
    assert response.status_code == 307, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def test_only_a_member_of_the_meetings_team_can_start(world: dict[str, Any]) -> None:
    response = signed_in(world, OUTSIDER).get(f"/api/auth/notion/start?meeting_id={MEETING}")
    assert response.status_code == 403
    assert world["store"]._entries == {}


def test_connecting_stores_the_workspace_bot_token(world: dict[str, Any]) -> None:
    client = signed_in(world)
    response = client.get(f"/api/auth/notion/callback?state={start(client)}&code=c")

    assert response.headers["location"].endswith(f"/meetings/{MEETING}/actions?notion=connected")
    row = world["saved"][TEAM]
    assert row["secret"] == "ntn_token"
    assert row["connected_by"] == ME
    assert row["config"] == {"workspace_id": "ws-1", "workspace_name": "Acme", "bot_id": "bot-1"}


@pytest.mark.parametrize("case", ["declined", "refused"])
def test_a_failed_connect_goes_back_to_the_screen(world: dict[str, Any], case: str) -> None:
    client = signed_in(world)
    state = start(client)
    if case == "refused":
        world["notion"].answer = PermissionDeniedError("Notion rejected the authorization code")
    extra = "&error=access_denied" if case == "declined" else "&code=c"

    response = client.get(f"/api/auth/notion/callback?state={state}{extra}")

    assert response.headers["location"].endswith("?notion=failed")
    assert world["saved"] == {}


def test_a_callback_from_another_browser_stores_nothing(world: dict[str, Any]) -> None:
    state = start(signed_in(world))
    other = signed_in(world)  # no state cookie
    assert other.get(f"/api/auth/notion/callback?state={state}&code=c").status_code == 403
    assert world["saved"] == {}


def test_a_deploy_that_cannot_store_the_token_fails_before_the_code_is_spent(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#704: the key is checked before Notion issues a token nothing keeps."""
    from autune_core.errors import ConfigurationError

    def unset() -> None:
        raise ConfigurationError("AUTUNE_ENCRYPTION_KEY is not set")

    client = signed_in(world)
    state = start(client)  # the key went between start and callback
    monkeypatch.setattr(auth_router_module, "ensure_configured", unset)
    response = client.get(f"/api/auth/notion/callback?state={state}&code=c")

    assert response.headers["location"].endswith("?notion=failed")
    assert world["notion"].exchanged == 0, "Notion was not asked"
    assert world["saved"] == {}


def test_a_deploy_that_cannot_store_the_token_sends_nobody_to_notion(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from autune_core.errors import ConfigurationError

    def unset() -> None:
        raise ConfigurationError("AUTUNE_ENCRYPTION_KEY is not set")

    monkeypatch.setattr(auth_router_module, "ensure_configured", unset)
    response = signed_in(world).get(f"/api/auth/notion/start?meeting_id={MEETING}")

    assert response.status_code == 303
    assert response.headers["location"].endswith("?notion=failed")
    assert world["store"]._entries == {}


def test_status_and_disconnect_are_for_members_only(world: dict[str, Any]) -> None:
    client = signed_in(world)
    client.get(f"/api/auth/notion/callback?state={start(client)}&code=c")

    assert client.get(f"/api/auth/notion?meeting_id={MEETING}").json() == {
        "connected": True,
        "workspace_name": "Acme",
    }
    outsider = signed_in(world, OUTSIDER)
    assert outsider.get(f"/api/auth/notion?meeting_id={MEETING}").status_code == 403

    assert client.post(f"/api/auth/notion/disconnect?meeting_id={MEETING}").json() == {
        "connected": False,
        "revoked": False,
    }
    assert world["saved"] == {}


def test_the_code_exchange_uses_basic_auth_and_keeps_the_code_out_of_errors() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        if seen["body"]["code"] == "bad-code":
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(
            200,
            json={
                "access_token": "ntn_x",
                "workspace_id": "w",
                "workspace_name": "W",
                "bot_id": "b",
            },
        )

    client = NotionOAuthClient(
        client_id="cid",
        client_secret="cs",
        redirect_uri="http://localhost:3000/api/auth/notion/callback",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.exchange_code("good").access_token == "ntn_x"
    assert b64decode(seen["auth"].split()[1]) == b"cid:cs"
    assert seen["body"]["grant_type"] == "authorization_code"
    with pytest.raises(PermissionDeniedError) as caught:
        client.exchange_code("bad-code")
    assert "bad-code" not in str(caught.value)
