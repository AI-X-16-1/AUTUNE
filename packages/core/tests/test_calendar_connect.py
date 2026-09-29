"""One-click Google Calendar connect (#435): a signed-in person's own grant,
through the sign-in flow's client, state binding and callback -- without a
network, a Redis or a Postgres."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from autune_core import SESSION_COOKIE, issue_token
from autune_core import auth_router as auth_router_module
from autune_core.auth_router import STATE_COOKIE
from autune_core.auth_router import router as auth_router
from autune_core.db import Base, get_session
from autune_core.entities import User
from autune_core.errors import AutuneError, PermissionDeniedError
from autune_core.oauth.google import (
    CALENDAR_SCOPE,
    GoogleGrant,
    GoogleIdentity,
    GoogleOAuthClient,
    get_google_client,
)
from autune_core.oauth.state import InMemoryStateStore, OAuthTransaction, get_state_store
from autune_core.user_integrations import UserIntegrationConfig

ME = "user_me"


class FakeGoogle:
    def __init__(self) -> None:
        self.grant = GoogleGrant(
            id_token="id-token",
            refresh_token="1//refresh",
            scopes=frozenset({"openid", CALENDAR_SCOPE}),
        )
        self.revoked: list[str] = []
        self.revoke_answer = True
        self.asked: dict[str, Any] = {}

    def authorization_url(self, *, state: str, nonce: str, **kw: Any) -> str:
        self.asked = kw
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}&nonce={nonce}"

    def exchange_grant(self, code: str) -> GoogleGrant:
        return self.grant

    def verify(self, id_token: str, *, nonce: str) -> GoogleIdentity:
        # The calendar consent asks for no ``email`` scope, so a calendar on
        # another Google account comes back without one -- and verify() then
        # refuses, as the real client does (#452 review). Sign-in is not here.
        raise PermissionDeniedError("Google account exposes no email address")

    def verify_request(self, id_token: str, *, nonce: str) -> dict[str, Any]:
        return {"sub": "another-google-account", "nonce": nonce}

    def revoke(self, token: str) -> bool:
        self.revoked.append(token)
        return self.revoke_answer


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[User.__table__])
    db = sessionmaker(bind=engine)()
    db.add(User(id=ME, email="me@example.com", display_name="Me"))
    db.add(User(id="user_other", email="o@example.com", display_name="Other"))
    db.commit()

    grants: dict[str, str] = {}

    def save(_s: Session, user_id: str, service: str, *, secret: str, config: dict) -> None:
        assert service == "calendar"
        assert config == {"calendar_id": "primary"}
        grants[user_id] = secret

    def load(_s: Session, user_id: str, service: str) -> UserIntegrationConfig | None:
        secret = grants.get(user_id)
        return None if secret is None else UserIntegrationConfig(service, user_id, secret)

    monkeypatch.setattr(auth_router_module, "save_user_integration", save)
    monkeypatch.setattr(auth_router_module, "load_user_integration", load)
    monkeypatch.setattr(
        auth_router_module, "disconnect_user_integration", lambda _s, uid, _svc: grants.pop(uid)
    )

    store = InMemoryStateStore()
    google = FakeGoogle()
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")

    @app.exception_handler(AutuneError)
    def _handle(_req: object, exc: AutuneError):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_state_store] = lambda: store
    app.dependency_overrides[get_google_client] = lambda: google
    return {"app": app, "store": store, "google": google, "grants": grants}


def signed_in(world: dict[str, Any], user_id: str = ME) -> TestClient:
    client = TestClient(world["app"], follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, issue_token(user_id))
    return client


def start(client: TestClient, redirect_to: str = "/meetings/m1/actions") -> str:
    response = client.get(f"/api/auth/google/calendar/start?redirect_to={redirect_to}")
    assert response.status_code == 307
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def callback(client: TestClient, state: str) -> httpx.Response:
    return client.get(f"/api/auth/google/callback?state={state}&code=the-code")


# --- start --------------------------------------------------------------------------


def test_connecting_needs_a_signed_in_person(world: dict[str, Any]) -> None:
    anonymous = TestClient(world["app"], follow_redirects=False)
    response = anonymous.get("/api/auth/google/calendar/start")
    assert response.status_code in (401, 403)
    assert world["store"]._entries == {}


def test_start_asks_google_for_the_calendar_offline_and_remembers_who_asked(
    world: dict[str, Any],
) -> None:
    client = signed_in(world)
    start(client)

    assert world["google"].asked == {"scope": f"openid {CALENDAR_SCOPE}", "offline": True}
    ((_expiry, txn),) = world["store"]._entries.values()
    assert (txn.purpose, txn.user_id) == ("calendar", ME)


# --- the callback ---------------------------------------------------------------------


def test_the_callback_stores_the_grant_for_the_person_who_started(world: dict[str, Any]) -> None:
    client = signed_in(world)
    response = callback(client, start(client))

    assert response.status_code == 303
    assert response.headers["location"].endswith("/meetings/m1/actions?calendar=connected")
    assert world["grants"] == {ME: "1//refresh"}
    assert SESSION_COOKIE not in response.cookies  # connecting is not signing in again


def test_a_callback_from_another_browser_stores_nothing(world: dict[str, Any]) -> None:
    """The sign-in flow's state binding covers the calendar too."""
    state = start(signed_in(world))
    other = signed_in(world, "user_other")  # has no state cookie for this flow

    assert callback(other, state).status_code == 403
    assert world["grants"] == {}


def _failed_back_to_the_screen(response: httpx.Response) -> bool:
    return response.status_code == 303 and response.headers["location"].endswith(
        "/meetings/m1/actions?calendar=failed"
    )


def test_an_unticked_calendar_scope_stores_nothing(world: dict[str, Any]) -> None:
    world["google"].grant = GoogleGrant("id-token", "1//refresh", frozenset({"openid"}))
    client = signed_in(world)

    assert _failed_back_to_the_screen(callback(client, start(client)))
    assert world["grants"] == {}


def test_no_refresh_token_stores_nothing(world: dict[str, Any]) -> None:
    world["google"].grant = GoogleGrant("id-token", None, frozenset({"openid", CALENDAR_SCOPE}))
    client = signed_in(world)

    assert _failed_back_to_the_screen(callback(client, start(client)))
    assert world["grants"] == {}


def test_declining_on_googles_screen_goes_back_to_the_screen(world: dict[str, Any]) -> None:
    """Google answers a decline with ``error=access_denied`` and no code."""
    client = signed_in(world)
    state = start(client)

    response = client.get(f"/api/auth/google/callback?state={state}&error=access_denied")

    assert _failed_back_to_the_screen(response)
    assert world["grants"] == {}


def test_the_state_cookie_is_cleared_after_a_calendar_callback(world: dict[str, Any]) -> None:
    client = signed_in(world)
    response = callback(client, start(client))
    cleared = [h for h in response.headers.get_list("set-cookie") if h.startswith(STATE_COOKIE)]
    assert cleared and "max-age=0" in cleared[0].lower()


# --- status and disconnect ----------------------------------------------------------


def test_status_is_the_persons_own(world: dict[str, Any]) -> None:
    world["grants"][ME] = "1//refresh"

    assert signed_in(world).get("/api/auth/google/calendar").json() == {"connected": True}
    assert signed_in(world, "user_other").get("/api/auth/google/calendar").json() == {
        "connected": False
    }


def test_a_calendar_on_an_account_without_an_email_claim_connects(
    world: dict[str, Any],
) -> None:
    """#452 review: the consent is ``openid`` + calendar, so another account's
    ID token carries no email; requiring one made every such connect a 403."""
    client = signed_in(world)
    response = callback(client, start(client))

    assert response.headers["location"].endswith("?calendar=connected")
    assert world["grants"] == {ME: "1//refresh"}


def test_reconnecting_revokes_the_grant_it_replaces(world: dict[str, Any]) -> None:
    world["grants"][ME] = "1//old"
    client = signed_in(world)

    callback(client, start(client))

    assert world["google"].revoked == ["1//old"]
    assert world["grants"] == {ME: "1//refresh"}


def test_reconnecting_with_the_same_token_keeps_it(world: dict[str, Any]) -> None:
    world["grants"][ME] = "1//refresh"
    client = signed_in(world)

    callback(client, start(client))

    assert world["google"].revoked == []


def test_disconnect_revokes_at_google_then_forgets(world: dict[str, Any]) -> None:
    world["grants"][ME] = "1//refresh"

    body = signed_in(world).post("/api/auth/google/calendar/disconnect").json()

    assert world["google"].revoked == ["1//refresh"]
    assert body == {"connected": False, "revoked": True}
    assert world["grants"] == {}


def test_disconnect_forgets_even_when_google_does_not_answer(world: dict[str, Any]) -> None:
    world["grants"][ME] = "1//refresh"
    world["google"].revoke_answer = False

    body = signed_in(world).post("/api/auth/google/calendar/disconnect").json()

    assert body == {"connected": False, "revoked": False}
    assert world["grants"] == {}


# --- the client and the state, at their own doors -------------------------------------


def _client(http: httpx.Client | None = None) -> GoogleOAuthClient:
    return GoogleOAuthClient(
        client_id="cid",
        client_secret="cs",
        redirect_uri="http://localhost:3000/api/auth/google/callback",
        http=http or httpx.Client(),
        jwks_client=object(),  # type: ignore[arg-type]
    )


def test_an_offline_url_asks_for_consent_and_keeps_earlier_scopes() -> None:
    query = parse_qs(
        urlsplit(
            _client().authorization_url(
                state="s", nonce="n", scope=f"openid {CALENDAR_SCOPE}", offline=True
            )
        ).query
    )
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["include_granted_scopes"] == ["true"]
    assert query["scope"] == [f"openid {CALENDAR_SCOPE}"]


def test_sign_in_urls_are_unchanged() -> None:
    query = parse_qs(urlsplit(_client().authorization_url(state="s", nonce="n")).query)
    assert query["access_type"] == ["online"]
    assert "include_granted_scopes" not in query


def test_exchange_grant_reads_the_refresh_token_and_granted_scopes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id_token": "jwt",
                "refresh_token": "1//r",
                "scope": f"openid {CALENDAR_SCOPE}",
            },
        )

    grant = _client(httpx.Client(transport=httpx.MockTransport(handler))).exchange_grant("c")

    assert (grant.id_token, grant.refresh_token) == ("jwt", "1//r")
    assert CALENDAR_SCOPE in grant.scopes


def test_a_transaction_from_before_this_change_reads_as_sign_in() -> None:
    old = json.dumps({"nonce": "n", "redirect_to": "/", "created_at": 1.0})
    txn = OAuthTransaction.from_json(old)
    assert (txn.purpose, txn.user_id) == ("sign_in", None)
    assert (
        OAuthTransaction.from_json(
            OAuthTransaction("n", "/", purpose="calendar", user_id=ME).to_json()
        ).user_id
        == ME
    )


def test_another_flows_state_is_not_a_google_sign_in(world: dict[str, Any]) -> None:
    """#452 review (inline): Jira, Notion and Slack keep their state in the same
    store. With cookie and query both set to such a state, the Google callback
    refuses instead of running a sign-in."""
    world["store"].put("st-jira", OAuthTransaction("", "/", purpose="jira", user_id=ME))
    exchanged: list[str] = []
    world["google"].exchange_code = lambda code: exchanged.append(code) or ""
    client = signed_in(world)
    client.cookies.set(STATE_COOKIE, "st-jira")

    response = callback(client, "st-jira")

    assert response.status_code == 403
    assert exchanged == []
    assert world["grants"] == {}
