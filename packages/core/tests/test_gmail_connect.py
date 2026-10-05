"""One-click Gmail send connect (#552): a signed-in person lets Autune send
mail as them -- ``gmail.send`` only -- through the same flow as their calendar
(#435), stored as a grant of its own. Without a network, a Redis or a Postgres."""

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
    GMAIL_SEND_SCOPE,
    GoogleGrant,
    get_google_client,
    get_google_integration_client,
)
from autune_core.oauth.state import InMemoryStateStore, get_state_store
from autune_core.user_integrations import USER_SERVICES, UserIntegrationConfig

ME = "user_me"


class FakeGoogle:
    def __init__(self) -> None:
        self.client_id = "sign-in-client"
        self.grant = GoogleGrant(
            id_token="id-token",
            refresh_token="1//gmail",
            scopes=frozenset({"openid", GMAIL_SEND_SCOPE}),
        )
        self.revoked: list[str] = []
        self.asked: dict[str, Any] = {}
        self.challenge: str | None = None
        self.exchanged: list[str | None] = []

    def authorization_url(self, *, state: str, nonce: str, **kw: Any) -> str:
        # The challenge is fresh on every request; kept apart so ``asked``
        # still compares to a fixed answer.
        self.challenge = kw.pop("code_challenge", None)
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
    # These tests store no encrypted secret; the check has its own tests below.
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


def start(client: TestClient, redirect_to: str = "/settings/members") -> str:
    response = client.get(f"/api/auth/google/gmail/start?redirect_to={redirect_to}")
    assert response.status_code == 307
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def callback(client: TestClient, state: str) -> httpx.Response:
    return client.get(f"/api/auth/google/callback?state={state}&code=the-code")


def test_gmail_send_is_a_service_the_store_accepts() -> None:
    assert "gmail_send" in USER_SERVICES
    assert "gmail" not in USER_SERVICES  # reading a mailbox waits on #431


def test_connecting_needs_a_signed_in_person(world: dict[str, Any]) -> None:
    anonymous = TestClient(world["app"], follow_redirects=False)
    assert anonymous.get("/api/auth/google/gmail/start").status_code in (401, 403)
    assert world["store"]._entries == {}


def test_start_asks_google_to_send_only_and_offline(world: dict[str, Any]) -> None:
    start(signed_in(world))

    assert world["google"].asked == {"scope": f"openid {GMAIL_SEND_SCOPE}", "offline": True}
    ((_expiry, txn),) = world["store"]._entries.values()
    assert (txn.purpose, txn.user_id) == ("gmail_send", ME)
    assert world["google"].challenge == s256(txn.code_verifier)


def s256(verifier: str | None) -> str:
    """RFC 7636's S256, written out here rather than imported, so the test
    checks the client's arithmetic instead of repeating it."""
    import base64
    import hashlib

    assert verifier
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# --- what #765 gave the calendar connect, for this one too (#760 review) ---------------


def test_the_code_is_exchanged_with_the_verifier_its_challenge_came_from(
    world: dict[str, Any],
) -> None:
    """PKCE binds the code to the request that asked for it (#704)."""
    client = signed_in(world)
    state = start(client)
    ((_expiry, txn),) = world["store"]._entries.values()

    callback(client, state)

    assert world["google"].exchanged == [txn.code_verifier]
    assert txn.code_verifier


def _failed_back_to_the_screen(response: httpx.Response) -> bool:
    return response.status_code == 303 and response.headers["location"].endswith(
        "/settings/members?gmail=failed"
    )


def _no_key() -> None:
    raise ConfigurationError("AUTUNE_ENCRYPTION_KEY is not set")


def test_a_deploy_that_cannot_store_the_grant_fails_before_the_code_is_spent(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty AUTUNE_ENCRYPTION_KEY must not cost a grant Google issued and
    nothing kept (#704) -- a grant to send mail least of all."""
    client = signed_in(world)
    state = start(client)  # the key went between start and callback
    monkeypatch.setattr(auth_router_module, "ensure_configured", _no_key)

    response = callback(client, state)

    assert _failed_back_to_the_screen(response)
    assert world["google"].exchanged == [], "Google was not asked"
    assert world["rows"] == {}


def test_a_deploy_that_cannot_store_the_grant_sends_nobody_to_google(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No consent screen that cannot succeed -- and the person goes back to
    the screen with Gmail's own answer, not the calendar's."""
    monkeypatch.setattr(auth_router_module, "ensure_configured", _no_key)

    response = signed_in(world).get("/api/auth/google/gmail/start?redirect_to=/settings/members")

    assert _failed_back_to_the_screen(response)
    assert world["store"]._entries == {}


def test_the_callback_stores_a_gmail_grant_and_leaves_the_calendar_alone(
    world: dict[str, Any],
) -> None:
    client = signed_in(world)
    response = callback(client, start(client))

    assert response.status_code == 303
    assert response.headers["location"].endswith("/settings/members?gmail=connected")
    grant = world["rows"][(ME, "gmail_send")]
    assert grant.secret == "1//gmail"
    assert grant.config == {"google_sub": "google-account", "client_id": "sign-in-client"}
    assert (ME, "calendar") not in world["rows"]


def test_a_grant_without_the_send_scope_stores_nothing(world: dict[str, Any]) -> None:
    """Ticking only the calendar's box on Google's screen is not a Gmail grant."""
    world["google"].grant = GoogleGrant(
        "id-token", "1//refresh", frozenset({"openid", CALENDAR_SCOPE})
    )
    client = signed_in(world)
    response = callback(client, start(client))

    assert response.headers["location"].endswith("/settings/members?gmail=failed")
    assert world["rows"] == {}


def test_status_and_disconnect_are_the_persons_own(world: dict[str, Any]) -> None:
    client = signed_in(world)
    assert client.get("/api/auth/google/gmail").json() == {
        "connected": False,
        "needs_reconnect": False,
    }
    callback(client, start(client))
    assert client.get("/api/auth/google/gmail").json()["connected"] is True

    answer = client.post("/api/auth/google/gmail/disconnect").json()

    assert answer == {"connected": False, "revoked": True}
    assert world["google"].revoked == ["1//gmail"]
    assert world["rows"] == {}


def _calendar_row(world: dict[str, Any], *, account: str, client: str) -> None:
    world["rows"][(ME, "calendar")] = UserIntegrationConfig(
        "calendar",
        ME,
        "1//calendar",
        {"calendar_id": "primary", "google_sub": account, "client_id": client},
    )


def test_a_revoke_that_may_end_the_calendar_says_so_on_the_calendar(
    world: dict[str, Any],
) -> None:
    """Google's revoke can end everything one account gave this client; the
    calendar must not go on looking connected (#760 review)."""
    client = signed_in(world)
    callback(client, start(client))
    _calendar_row(world, account="google-account", client="sign-in-client")

    client.post("/api/auth/google/gmail/disconnect")

    assert world["rows"][(ME, "calendar")].config["grant_revoked"] is True
    assert client.get("/api/auth/google/calendar").json() == {
        "connected": True,
        "needs_reconnect": True,
    }


def test_a_calendar_on_another_account_is_left_alone(world: dict[str, Any]) -> None:
    client = signed_in(world)
    callback(client, start(client))
    _calendar_row(world, account="another-google-account", client="sign-in-client")

    client.post("/api/auth/google/gmail/disconnect")

    assert "grant_revoked" not in world["rows"][(ME, "calendar")].config
    assert client.get("/api/auth/google/calendar").json()["needs_reconnect"] is False


def test_connecting_again_clears_the_mark(world: dict[str, Any]) -> None:
    client = signed_in(world)
    world["rows"][(ME, "gmail_send")] = UserIntegrationConfig(
        "gmail_send",
        ME,
        "1//old",
        {"google_sub": "google-account", "client_id": "sign-in-client", "grant_revoked": True},
    )
    assert client.get("/api/auth/google/gmail").json()["needs_reconnect"] is True

    callback(client, start(client))

    assert client.get("/api/auth/google/gmail").json() == {
        "connected": True,
        "needs_reconnect": False,
    }


def test_a_grant_that_comes_back_with_the_calendars_scope_too_is_refused(
    world: dict[str, Any],
) -> None:
    """A merged token would let the Gmail row write the calendar (#760 review)."""
    world["google"].grant = GoogleGrant(
        "id-token", "1//merged", frozenset({"openid", GMAIL_SEND_SCOPE, CALENDAR_SCOPE})
    )
    client = signed_in(world)

    response = callback(client, start(client))

    assert response.headers["location"].endswith("/settings/members?gmail=failed")
    assert world["rows"] == {}
