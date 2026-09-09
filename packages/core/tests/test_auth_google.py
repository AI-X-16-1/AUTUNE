"""Google sign-in: the OAuth client, the state store, the user upsert, and the
/api/auth router end to end — all without a network, a Redis, or a Postgres.
"""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from autune_core import SESSION_COOKIE, issue_token
from autune_core.auth_router import router as auth_router
from autune_core.auth_service import upsert_user_from_google
from autune_core.db import Base, get_session
from autune_core.entities import User
from autune_core.errors import AutuneError, PermissionDeniedError
from autune_core.oauth.google import GoogleIdentity, GoogleOAuthClient, get_google_client
from autune_core.oauth.state import (
    InMemoryStateStore,
    OAuthTransaction,
    RedisStateStore,
    get_state_store,
)

# --------------------------------------------------------------------------- #
# State store
# --------------------------------------------------------------------------- #


def test_state_store_pop_is_one_shot() -> None:
    store = InMemoryStateStore()
    store.put("state-1", OAuthTransaction(nonce="n", redirect_to="/"))

    assert store.pop("state-1") is not None
    assert store.pop("state-1") is None  # a replayed callback finds nothing


def test_state_store_unknown_state_is_none() -> None:
    assert InMemoryStateStore().pop("never-seen") is None


def test_state_store_honours_expiry() -> None:
    store = InMemoryStateStore(ttl_seconds=0)
    store.put("state-1", OAuthTransaction(nonce="n", redirect_to="/"))
    time.sleep(0.01)
    assert store.pop("state-1") is None


def test_redis_state_store_round_trips_with_a_fake_client() -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        def set(self, key: str, value: str, ex: int | None = None) -> None:
            self.store[key] = value

        def pipeline(self) -> FakeRedis:
            self._ops: list[tuple[str, str]] = []
            return self

        def get(self, key: str) -> None:
            self._ops.append(("get", key))

        def delete(self, key: str) -> None:
            self._ops.append(("delete", key))

        def execute(self) -> list[object]:
            out: list[object] = []
            for op, key in self._ops:
                if op == "get":
                    out.append(self.store.get(key))
                else:
                    out.append(self.store.pop(key, None) is not None)
            return out

    store = RedisStateStore(FakeRedis())  # type: ignore[arg-type]
    store.put("s", OAuthTransaction(nonce="abc", redirect_to="/actions"))
    txn = store.pop("s")
    assert txn is not None and txn.nonce == "abc" and txn.redirect_to == "/actions"
    assert store.pop("s") is None


# --------------------------------------------------------------------------- #
# GoogleOAuthClient
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _StubJWKSClient:
    def __init__(self, public_key: object) -> None:
        self._key = public_key

    def get_signing_key_from_jwt(self, token: str) -> object:
        return type("Key", (), {"key": self._key})()


def _client(rsa_key: rsa.RSAPrivateKey, *, http: httpx.Client | None = None) -> GoogleOAuthClient:
    return GoogleOAuthClient(
        client_id="client-123.apps.googleusercontent.com",
        client_secret="secret",
        redirect_uri="http://localhost:8000/api/auth/google/callback",
        http=http or httpx.Client(),
        jwks_client=_StubJWKSClient(rsa_key.public_key()),  # type: ignore[arg-type]
    )


def _id_token(rsa_key: rsa.RSAPrivateKey, **overrides: object) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "iss": "https://accounts.google.com",
        "aud": "client-123.apps.googleusercontent.com",
        "sub": "google-sub-1",
        "email": "dev@example.com",
        "email_verified": True,
        "name": "Dev Example",
        "nonce": "the-nonce",
        "iat": now,
        "exp": now + 3600,
    }
    payload.update(overrides)
    return jwt.encode(payload, rsa_key, algorithm="RS256")


def test_authorization_url_carries_the_request_parameters(rsa_key: rsa.RSAPrivateKey) -> None:
    url = _client(rsa_key).authorization_url(state="st", nonce="no")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=client-123" in url
    assert "state=st" in url and "nonce=no" in url
    assert "scope=openid+email+profile" in url


def test_unconfigured_client_refuses_to_construct() -> None:
    with pytest.raises(AutuneError):
        GoogleOAuthClient(client_id="", client_secret="", redirect_uri="")


def test_verify_accepts_a_well_formed_token(rsa_key: rsa.RSAPrivateKey) -> None:
    identity = _client(rsa_key).verify(_id_token(rsa_key), nonce="the-nonce")
    assert identity.sub == "google-sub-1"
    assert identity.email == "dev@example.com"
    assert identity.email_verified is True
    assert identity.name == "Dev Example"


def test_verify_rejects_a_nonce_mismatch(rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(PermissionDeniedError):
        _client(rsa_key).verify(_id_token(rsa_key), nonce="different")


def test_verify_rejects_a_wrong_audience(rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(PermissionDeniedError):
        _client(rsa_key).verify(_id_token(rsa_key, aud="someone-else"), nonce="the-nonce")


def test_verify_rejects_a_foreign_issuer(rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(PermissionDeniedError):
        _client(rsa_key).verify(_id_token(rsa_key, iss="https://evil.example"), nonce="the-nonce")


def test_verify_rejects_an_expired_token(rsa_key: rsa.RSAPrivateKey) -> None:
    stale = _id_token(rsa_key, exp=int(time.time()) - 10)
    with pytest.raises(PermissionDeniedError):
        _client(rsa_key).verify(stale, nonce="the-nonce")


def test_exchange_code_returns_the_id_token(rsa_key: rsa.RSAPrivateKey) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://oauth2.googleapis.com/token"
        return httpx.Response(200, json={"id_token": "an.id.token", "access_token": "ignored"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert _client(rsa_key, http=http).exchange_code("auth-code") == "an.id.token"


def test_exchange_code_maps_a_google_rejection_to_permission_denied(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    http = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(400, json={})))
    with pytest.raises(PermissionDeniedError):
        _client(rsa_key, http=http).exchange_code("auth-code")


# --------------------------------------------------------------------------- #
# upsert_user_from_google
# --------------------------------------------------------------------------- #


@pytest.fixture
def db() -> Session:
    # One shared in-memory connection so the TestClient's worker thread sees the
    # same schema and rows as the test thread.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__])
    with sessionmaker(bind=engine)() as session:
        yield session


def _identity(**kw: object) -> GoogleIdentity:
    base = {
        "sub": "sub-1",
        "email": "a@example.com",
        "email_verified": True,
        "name": "A",
        "picture": None,
    }
    base.update(kw)
    return GoogleIdentity(**base)  # type: ignore[arg-type]


def test_upsert_creates_a_new_user(db: Session) -> None:
    user = upsert_user_from_google(db, _identity())
    assert user.id.startswith("user_")
    assert user.google_sub == "sub-1"
    assert user.last_login_at is not None


def test_upsert_is_idempotent_on_google_sub(db: Session) -> None:
    first = upsert_user_from_google(db, _identity(name="Old"))
    second = upsert_user_from_google(db, _identity(name="New"))
    assert first.id == second.id
    assert second.display_name == "New"


def test_upsert_links_an_existing_magic_link_user_by_email(db: Session) -> None:
    existing = User(id="user_pre", email="a@example.com", display_name="Pre")
    db.add(existing)
    db.flush()

    user = upsert_user_from_google(db, _identity())
    assert user.id == "user_pre"
    assert user.google_sub == "sub-1"


# --------------------------------------------------------------------------- #
# /api/auth router
# --------------------------------------------------------------------------- #


class FakeGoogleClient:
    def __init__(self, identity: GoogleIdentity) -> None:
        self.identity = identity

    def authorization_url(self, *, state: str, nonce: str) -> str:
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}&nonce={nonce}"

    def exchange_code(self, code: str) -> str:
        return "fake-id-token"

    def verify(self, id_token: str, *, nonce: str) -> GoogleIdentity:
        return self.identity


@pytest.fixture
def api(db: Session) -> tuple[TestClient, dict[str, object]]:
    state_store = InMemoryStateStore()
    holder: dict[str, object] = {"identity": _identity(), "state_store": state_store}

    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")

    @app.exception_handler(AutuneError)
    def _handle(_req: object, exc: AutuneError):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_state_store] = lambda: state_store
    app.dependency_overrides[get_google_client] = lambda: FakeGoogleClient(
        holder["identity"]  # type: ignore[arg-type]
    )

    client = TestClient(app, follow_redirects=False)
    return client, holder


def test_start_redirects_to_google_and_stashes_state(
    api: tuple[TestClient, dict[str, object]],
) -> None:
    client, holder = api
    response = client.get("/api/auth/google/start?redirect_to=/actions")

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://accounts.google.com/")
    store = holder["state_store"]
    assert len(store._entries) == 1  # type: ignore[attr-defined]


def test_start_refuses_an_offsite_redirect_target(
    api: tuple[TestClient, dict[str, object]],
) -> None:
    client, holder = api
    client.get("/api/auth/google/start?redirect_to=https://evil.test/phish")
    ((_expiry, txn),) = holder["state_store"]._entries.values()  # type: ignore[attr-defined]
    assert txn.redirect_to == "/"


def _complete_login(client: TestClient, state_store: InMemoryStateStore) -> httpx.Response:
    start = client.get("/api/auth/google/start?redirect_to=/dashboard")
    location = start.headers["location"]
    state = location.split("state=", 1)[1].split("&", 1)[0]
    return client.get(f"/api/auth/google/callback?state={state}&code=abc")


def test_callback_signs_the_user_in_and_sets_a_cookie(
    api: tuple[TestClient, dict[str, object]], db: Session
) -> None:
    client, holder = api
    response = _complete_login(client, holder["state_store"])  # type: ignore[arg-type]

    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:3000/dashboard"
    assert SESSION_COOKIE in response.cookies

    assert db.query(User).filter(User.google_sub == "sub-1").one()


def test_callback_rejects_an_unknown_state(
    api: tuple[TestClient, dict[str, object]],
) -> None:
    client, _ = api
    response = client.get("/api/auth/google/callback?state=forged&code=abc")
    assert response.status_code == 403


def test_callback_rejects_an_unverified_email(
    api: tuple[TestClient, dict[str, object]],
) -> None:
    client, holder = api
    holder["identity"] = _identity(email_verified=False)
    response = _complete_login(client, holder["state_store"])  # type: ignore[arg-type]
    assert response.status_code == 403


def test_me_requires_a_session_and_then_returns_the_user(
    api: tuple[TestClient, dict[str, object]], db: Session
) -> None:
    client, _ = api
    assert client.get("/api/auth/me").status_code == 403

    user = upsert_user_from_google(db, _identity())
    response = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {issue_token(user.id)}"}
    )
    assert response.status_code == 200
    assert response.json()["email"] == "a@example.com"


def test_me_accepts_the_session_cookie(
    api: tuple[TestClient, dict[str, object]], db: Session
) -> None:
    client, _ = api
    user = upsert_user_from_google(db, _identity())
    client.cookies.set(SESSION_COOKIE, issue_token(user.id))
    assert client.get("/api/auth/me").status_code == 200


def test_logout_clears_the_cookie(api: tuple[TestClient, dict[str, object]]) -> None:
    client, _ = api
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    assert 'autune_session=""' in response.headers.get("set-cookie", "") or (
        "autune_session=;" in response.headers.get("set-cookie", "")
    )
