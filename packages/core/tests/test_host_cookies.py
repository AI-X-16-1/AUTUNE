"""The session and OAuth state cookies outside local development, and the
``Origin`` guard.

``autune.cloud`` (the landing page, with an ad script) and ``app.autune.cloud``
are one site, so ``SameSite`` does not separate them. Two things do: a
``__Host-`` cookie a sibling name cannot set, and a refusal of a state-changing
request that names another host in ``Origin``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Response
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from autune_core import auth, auth_router
from autune_core.origin_guard import OriginGuard


@pytest.fixture
def secure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(session_cookie_secure=True)
    monkeypatch.setattr(auth, "get_settings", lambda: fake)
    monkeypatch.setattr(auth_router, "get_settings", lambda: fake)


@pytest.fixture
def local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(session_cookie_secure=False)
    monkeypatch.setattr(auth, "get_settings", lambda: fake)
    monkeypatch.setattr(auth_router, "get_settings", lambda: fake)


def _set_cookie(response: Response) -> str:
    return response.headers["set-cookie"]


# --- the session cookie ---------------------------------------------------


def test_session_cookie_keeps_its_name_in_local_development(local_env: None) -> None:
    assert auth.session_cookie_name() == "autune_session"


def test_session_cookie_takes_the_host_prefix_where_it_is_secure(secure_env: None) -> None:
    assert auth.session_cookie_name() == "__Host-autune_session"

    response = Response()
    auth.set_session_cookie(response, "token")
    header = _set_cookie(response)
    assert header.startswith("__Host-autune_session=token")
    # What `__Host-` demands, or a browser drops the cookie: Secure, Path=/,
    # and no Domain.
    assert "Secure" in header
    assert "Path=/" in header
    assert "Domain" not in header
    assert "HttpOnly" in header


def test_sign_out_clears_the_prefixed_cookie(secure_env: None) -> None:
    response = Response()
    auth.clear_session_cookie(response)
    header = _set_cookie(response)
    assert header.startswith("__Host-autune_session=")
    assert "Max-Age=0" in header
    assert "Path=/" in header
    assert "Secure" in header


def _cookie_echo_app() -> FastAPI:
    app = FastAPI()

    @app.get("/session")
    def session(value: Annotated[str | None, Depends(auth.session_cookie_value)]) -> dict:
        return {"value": value}

    return app


def test_the_unprefixed_cookie_is_not_read_where_it_is_secure(secure_env: None) -> None:
    """A script on a sibling name can set `autune_session` for the whole
    site. It must not be taken for the session."""
    client = TestClient(_cookie_echo_app())
    client.cookies.set("autune_session", "planted-by-a-sibling")
    assert client.get("/session").json() == {"value": None}

    client.cookies.set("__Host-autune_session", "ours")
    assert client.get("/session").json() == {"value": "ours"}


def test_the_plain_cookie_is_read_in_local_development(local_env: None) -> None:
    client = TestClient(_cookie_echo_app())
    client.cookies.set("autune_session", "dev")
    assert client.get("/session").json() == {"value": "dev"}


# --- the OAuth state cookie -----------------------------------------------


def test_state_cookie_is_prefixed_and_on_the_root_path_where_it_is_secure(
    secure_env: None,
) -> None:
    response = Response()
    auth_router._set_state_cookie(response, "google", "s", local_path="/api/auth/google/callback")
    header = _set_cookie(response)
    assert header.startswith("__Host-autune_oauth_state_google=s")
    assert "Path=/;" in header or header.endswith("Path=/") or "Path=/," in header
    assert "Path=/api" not in header
    assert "Secure" in header
    assert "Domain" not in header


def test_each_provider_has_a_state_cookie_of_its_own(secure_env: None) -> None:
    names = {auth_router._state_cookie_name(p) for p in ("google", "jira", "notion", "slack")}
    assert len(names) == 4
    assert all(name.startswith("__Host-") for name in names)


def test_state_cookie_keeps_its_name_and_callback_path_in_local_development(
    local_env: None,
) -> None:
    response = Response()
    auth_router._set_state_cookie(response, "jira", "s", local_path="/api/auth/jira/callback")
    header = _set_cookie(response)
    assert header.startswith("autune_oauth_state=s")
    assert "Path=/api/auth/jira/callback" in header


def test_state_cookie_is_cleared_with_the_attributes_it_was_set_with(secure_env: None) -> None:
    response = Response()
    auth_router._clear_state_cookie(response, "slack", local_path="/api/auth/slack/callback")
    header = _set_cookie(response)
    assert header.startswith("__Host-autune_oauth_state_slack=")
    assert "Max-Age=0" in header
    assert "Path=/" in header
    assert "Secure" in header


def test_a_planted_state_cookie_is_not_read_where_it_is_secure(secure_env: None) -> None:
    app = FastAPI()

    @app.get("/state")
    def state(
        value: Annotated[str | None, Depends(auth_router._state_cookie_reader("google"))],
    ) -> dict:
        return {"value": value}

    client = TestClient(app)
    client.cookies.set("autune_oauth_state", "planted")
    assert client.get("/state").json() == {"value": None}
    client.cookies.set("__Host-autune_oauth_state_google", "ours")
    assert client.get("/state").json() == {"value": "ours"}


# --- the Origin guard -----------------------------------------------------

APP_HOST = "app.autune.cloud"


def _guarded_app(*, enforce: bool = True, allowed: tuple[str, ...] = ()) -> FastAPI:
    app = FastAPI()

    @app.get("/thing")
    def read() -> dict:
        return {"ok": True}

    @app.post("/thing")
    def change() -> dict:
        return {"ok": True}

    @app.post("/api/slack/events")
    def slack() -> dict:
        return {"ok": True}

    @app.websocket("/live")
    async def live(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    app.add_middleware(OriginGuard, enforce=enforce, allowed_origins=allowed)
    return app


def _client(**kwargs: object) -> TestClient:
    return TestClient(_guarded_app(**kwargs), base_url=f"https://{APP_HOST}")  # type: ignore[arg-type]


def test_a_post_from_the_same_host_goes_through() -> None:
    response = _client().post("/thing", headers={"Origin": f"https://{APP_HOST}"})
    assert response.status_code == 200


def test_a_post_from_a_sibling_name_is_refused() -> None:
    response = _client().post("/thing", headers={"Origin": "https://autune.cloud"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_a_post_from_another_site_is_refused() -> None:
    response = _client().post("/thing", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_origin_null_is_refused() -> None:
    response = _client().post("/thing", headers={"Origin": "null"})
    assert response.status_code == 403


def test_a_post_without_origin_goes_through() -> None:
    """Slack's servers post to /api/slack/events with none; a signature, not a
    cookie, authenticates them."""
    assert _client().post("/api/slack/events").status_code == 200
    assert _client().post("/thing").status_code == 200


def test_a_get_from_another_site_is_not_looked_at() -> None:
    response = _client().get("/thing", headers={"Origin": "https://autune.cloud"})
    assert response.status_code == 200


def test_the_port_does_not_make_a_host_another_host() -> None:
    response = _client().post("/thing", headers={"Origin": f"https://{APP_HOST}:443"})
    assert response.status_code == 200


def test_a_listed_origin_goes_through() -> None:
    client = _client(allowed=("https://staging.autune.cloud",))
    assert (
        client.post("/thing", headers={"Origin": "https://staging.autune.cloud"}).status_code == 200
    )
    assert client.post("/thing", headers={"Origin": "https://autune.cloud"}).status_code == 403


def test_the_guard_is_off_when_it_is_not_enforced() -> None:
    client = _client(enforce=False)
    assert client.post("/thing", headers={"Origin": "https://autune.cloud"}).status_code == 200


def test_a_websocket_handshake_from_a_sibling_name_is_refused() -> None:
    client = _client()
    headers = {"Host": APP_HOST, "Origin": "https://autune.cloud"}
    with (
        pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)),
        client.websocket_connect("/live", headers=headers),
    ):
        pass


def test_a_websocket_handshake_from_the_same_host_goes_through() -> None:
    client = _client()
    # The test client opens a socket to `testserver`; the browser's Host is
    # the app's, so it is set here.
    headers = {"Host": APP_HOST, "Origin": f"https://{APP_HOST}"}
    with client.websocket_connect("/live", headers=headers) as ws:
        assert ws.receive_text() == "hello"
