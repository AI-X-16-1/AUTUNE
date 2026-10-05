"""One-click Drive connect (#817): a signed-in person lets Autune read the
Drive files they pick for it -- ``drive.file`` only -- through the same flow as
their calendar (#435) and their Gmail send grant (#552), stored as a grant of
its own. Without a network, a Redis or a Postgres."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from autune_core import SESSION_COOKIE, issue_token
from autune_core import auth_router as auth_router_module
from autune_core.auth_router import router as auth_router
from autune_core.db import Base, get_session
from autune_core.entities import User
from autune_core.errors import AutuneError, ConfigurationError
from autune_core.oauth.google import (
    CALENDAR_SCOPE,
    DRIVE_FILE_SCOPE,
    GMAIL_SEND_SCOPE,
    GoogleGrant,
    get_google_client,
    get_google_integration_client,
)
from autune_core.oauth.state import InMemoryStateStore, get_state_store
from autune_core.user_integrations import (
    GOOGLE_SERVICES,
    USER_SERVICES,
    UserIntegrationConfig,
)

ME = "user_me"
BACK = "/dev-drive-preview"


class FakeGoogle:
    def __init__(self) -> None:
        self.client_id = "sign-in-client"
        self.grant = GoogleGrant(
            id_token="id-token",
            refresh_token="1//drive",
            scopes=frozenset({"openid", DRIVE_FILE_SCOPE}),
        )
        self.revoked: list[str] = []
        self.asked: dict[str, Any] = {}
        self.exchanged: list[str | None] = []

    def authorization_url(self, *, state: str, nonce: str, **kw: Any) -> str:
        kw.pop("code_challenge", None)
        self.asked = kw
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}&nonce={nonce}"

    def exchange_grant(self, code: str, *, code_verifier: str | None = None) -> GoogleGrant:
        self.exchanged.append(code_verifier)
        return self.grant

    def verify_request(self, id_token: str, *, nonce: str) -> dict[str, Any]:
        return {"sub": "google-account", "nonce": nonce}

    def revoke(self, token: str) -> bool:
        self.revoked.append(token)
        return True


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[User.__table__])
    db = sessionmaker(bind=engine)()
    db.add(User(id=ME, email="me@example.com", display_name="Me"))
    db.commit()

    rows: dict[tuple[str, str], UserIntegrationConfig] = {}

    def save(_s: Session, user_id: str, service: str, *, secret: str, config: dict) -> None:
        rows[(user_id, service)] = UserIntegrationConfig(service, user_id, secret, dict(config))

    def load(_s: Session, user_id: str, service: str) -> UserIntegrationConfig | None:
        return rows.get((user_id, service))

    monkeypatch.setattr(auth_router_module, "save_user_integration", save)
    monkeypatch.setattr(auth_router_module, "load_user_integration", load)
    monkeypatch.setattr(
        auth_router_module,
        "disconnect_user_integration",
        lambda _s, uid, svc: rows.pop((uid, svc), None),
    )
    monkeypatch.setattr(auth_router_module, "ensure_configured", lambda: None)

    store = InMemoryStateStore()
    google = FakeGoogle()
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")

    @app.exception_handler(AutuneError)
    def _handle(_req: object, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_state_store] = lambda: store
    app.dependency_overrides[get_google_client] = lambda: google
    app.dependency_overrides[get_google_integration_client] = lambda: None
    return {"app": app, "store": store, "google": google, "rows": rows}


def signed_in(world: dict[str, Any]) -> TestClient:
    client = TestClient(world["app"], follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, issue_token(ME))
    return client


def start(client: TestClient) -> str:
    response = client.get(f"/api/auth/google/drive/start?redirect_to={BACK}")
    assert response.status_code == 307
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def callback(client: TestClient, state: str) -> httpx.Response:
    return client.get(f"/api/auth/google/callback?state={state}&code=the-code")


def test_drive_is_a_service_the_store_accepts_and_a_google_grant() -> None:
    assert "drive" in USER_SERVICES
    assert "drive" in GOOGLE_SERVICES, "revoked at Google when the account is deleted"


def test_the_scope_is_the_picked_files_and_not_the_whole_drive() -> None:
    """``drive.readonly`` reads every file the person can open and is a
    restricted scope. This grant must never become that by an edit."""
    assert DRIVE_FILE_SCOPE == "https://www.googleapis.com/auth/drive.file"


def test_connecting_needs_a_signed_in_person(world: dict[str, Any]) -> None:
    anonymous = TestClient(world["app"], follow_redirects=False)
    assert anonymous.get("/api/auth/google/drive/start").status_code in (401, 403)
    assert anonymous.get("/api/auth/google/drive").status_code in (401, 403)
    assert anonymous.post("/api/auth/google/drive/disconnect").status_code in (401, 403)
    assert world["store"]._entries == {}


def test_start_asks_google_for_the_picked_files_only_and_offline(world: dict[str, Any]) -> None:
    start(signed_in(world))

    assert world["google"].asked == {"scope": f"openid {DRIVE_FILE_SCOPE}", "offline": True}
    ((_expiry, txn),) = world["store"]._entries.values()
    assert (txn.purpose, txn.user_id) == ("drive", ME)


def test_the_callback_stores_a_drive_grant_and_no_other(world: dict[str, Any]) -> None:
    client = signed_in(world)
    response = callback(client, start(client))

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"{BACK}?drive=connected")
    grant = world["rows"][(ME, "drive")]
    assert grant.secret == "1//drive"
    assert grant.config == {"google_sub": "google-account", "client_id": "sign-in-client"}
    assert set(world["rows"]) == {(ME, "drive")}


def test_a_grant_without_the_drive_scope_stores_nothing(world: dict[str, Any]) -> None:
    """Unticking Drive's box on Google's screen is not a Drive grant."""
    world["google"].grant = GoogleGrant("id-token", "1//refresh", frozenset({"openid"}))
    client = signed_in(world)
    response = callback(client, start(client))

    assert response.headers["location"].endswith(f"{BACK}?drive=failed")
    assert world["rows"] == {}


@pytest.mark.parametrize("other", [CALENDAR_SCOPE, GMAIL_SEND_SCOPE])
def test_a_grant_that_comes_back_with_another_grants_scope_too_is_refused(
    world: dict[str, Any], other: str
) -> None:
    """A merged token would let the Drive row write a calendar or send mail."""
    world["google"].grant = GoogleGrant(
        "id-token", "1//merged", frozenset({"openid", DRIVE_FILE_SCOPE, other})
    )
    client = signed_in(world)

    response = callback(client, start(client))

    assert response.headers["location"].endswith(f"{BACK}?drive=failed")
    assert world["rows"] == {}


def test_a_gmail_grant_that_comes_back_with_drives_scope_is_refused_too(
    world: dict[str, Any],
) -> None:
    """The same rule from the other side: adding a third kind must not let a
    Gmail row read files."""
    world["google"].grant = GoogleGrant(
        "id-token", "1//merged", frozenset({"openid", GMAIL_SEND_SCOPE, DRIVE_FILE_SCOPE})
    )
    client = signed_in(world)
    response = client.get("/api/auth/google/gmail/start?redirect_to=/settings/members")
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]

    answer = callback(client, state)

    assert answer.headers["location"].endswith("/settings/members?gmail=failed")
    assert world["rows"] == {}


def test_a_deploy_that_cannot_store_the_grant_sends_nobody_to_google(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_key() -> None:
        raise ConfigurationError("AUTUNE_ENCRYPTION_KEY is not set")

    monkeypatch.setattr(auth_router_module, "ensure_configured", no_key)

    response = signed_in(world).get(f"/api/auth/google/drive/start?redirect_to={BACK}")

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"{BACK}?drive=failed")
    assert world["store"]._entries == {}


def test_status_and_disconnect_are_the_persons_own(world: dict[str, Any]) -> None:
    client = signed_in(world)
    assert client.get("/api/auth/google/drive").json() == {
        "connected": False,
        "needs_reconnect": False,
    }
    callback(client, start(client))
    assert client.get("/api/auth/google/drive").json()["connected"] is True

    answer = client.post("/api/auth/google/drive/disconnect").json()

    assert answer == {"connected": False, "revoked": True}
    assert world["google"].revoked == ["1//drive"]
    assert world["rows"] == {}


def test_a_revoke_that_may_end_the_calendar_says_so_on_the_calendar(
    world: dict[str, Any],
) -> None:
    """Google's revoke can end everything one account gave this client; the
    calendar must not go on looking connected (the rule #760 set)."""
    client = signed_in(world)
    callback(client, start(client))
    world["rows"][(ME, "calendar")] = UserIntegrationConfig(
        "calendar",
        ME,
        "1//calendar",
        {"calendar_id": "primary", "google_sub": "google-account", "client_id": "sign-in-client"},
    )

    client.post("/api/auth/google/drive/disconnect")

    assert world["rows"][(ME, "calendar")].config["grant_revoked"] is True
    assert client.get("/api/auth/google/calendar").json() == {
        "connected": True,
        "needs_reconnect": True,
    }
