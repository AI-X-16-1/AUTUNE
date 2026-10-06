"""Google sign-in: the OAuth client, the state store, the user upsert, and the
/api/auth router end to end — all without a network, a Redis, or a Postgres.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import SESSION_COOKIE, issue_token
from autune_core import auth_router as auth_router_module
from autune_core.auth_router import STATE_COOKIE
from autune_core.auth_router import router as auth_router
from autune_core.auth_service import upsert_user_from_google
from autune_core.db import Base, get_session
from autune_core.entities import Team, TeamMember, User
from autune_core.errors import AutuneError, PermissionDeniedError
from autune_core.oauth.google import (
    CLOCK_SKEW_SECONDS,
    GoogleIdentity,
    GoogleOAuthClient,
    get_google_client,
    pkce_pair,
)
from autune_core.oauth.state import (
    InMemoryStateStore,
    OAuthTransaction,
    RedisStateStore,
    get_state_store,
)
from autune_core.settings import get_settings

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


def test_a_transaction_keeps_its_pkce_verifier_through_json() -> None:
    raw = OAuthTransaction(nonce="n", redirect_to="/", code_verifier="v" * 43).to_json()
    assert OAuthTransaction.from_json(raw).code_verifier == "v" * 43


def test_a_transaction_stored_before_pkce_reads_without_a_verifier() -> None:
    """One started before the deploy is finished after it: its code was
    issued without a challenge and must be exchanged without a verifier."""
    raw = '{"nonce": "n", "redirect_to": "/", "created_at": 1.0, "purpose": "sign_in"}'
    assert OAuthTransaction.from_json(raw).code_verifier is None


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


def test_pkce_pair_is_an_s256_challenge_of_a_fresh_verifier() -> None:
    verifier, challenge = pkce_pair()
    digest = hashlib.sha256(verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).decode().rstrip("=")
    # RFC 7636 section 4.1: 43 to 128 unreserved characters.
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier)
    assert pkce_pair()[0] != verifier


def test_authorization_url_carries_a_pkce_challenge_when_given_one(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    query = parse_qs(
        urlsplit(
            _client(rsa_key).authorization_url(state="st", nonce="no", code_challenge="ch")
        ).query
    )
    assert query["code_challenge"] == ["ch"]
    assert query["code_challenge_method"] == ["S256"]


def test_authorization_url_without_a_challenge_asks_for_no_pkce(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    query = parse_qs(urlsplit(_client(rsa_key).authorization_url(state="st", nonce="no")).query)
    assert "code_challenge" not in query and "code_challenge_method" not in query


def test_exchange_sends_the_verifier_only_when_there_is_one(rsa_key: rsa.RSAPrivateKey) -> None:
    sent: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"id_token": "an.id.token"})

    client = _client(rsa_key, http=httpx.Client(transport=httpx.MockTransport(handler)))
    client.exchange_code("auth-code", code_verifier="the-verifier")
    client.exchange_code("auth-code")

    assert sent[0]["code_verifier"] == ["the-verifier"]
    assert "code_verifier" not in sent[1]


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
    stale = _id_token(rsa_key, exp=int(time.time()) - CLOCK_SKEW_SECONDS - 60)
    with pytest.raises(PermissionDeniedError):
        _client(rsa_key).verify(stale, nonce="the-nonce")


def test_verify_accepts_a_token_issued_just_ahead_of_this_clock(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    """#618: a PC 0.9 s behind Google read a fresh token's ``iat`` as in the
    future and refused every sign-in."""
    fresh = _id_token(rsa_key, iat=int(time.time()) + 5)

    identity = _client(rsa_key).verify(fresh, nonce="the-nonce")

    assert identity.sub == "google-sub-1"


def test_verify_rejects_a_token_issued_beyond_the_skew(rsa_key: rsa.RSAPrivateKey) -> None:
    ahead = _id_token(rsa_key, iat=int(time.time()) + CLOCK_SKEW_SECONDS + 60)

    with capture_logs() as logs, pytest.raises(PermissionDeniedError):
        _client(rsa_key).verify(ahead, nonce="the-nonce")

    (entry,) = [e for e in logs if e["event"] == "auth_google_id_token_rejected"]
    assert entry["reason"] == "ImmatureSignatureError", "the log names which check refused it"
    assert "dev@example.com" not in repr(logs)
    assert ahead not in repr(logs)


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


@pytest.mark.parametrize(
    ("body", "logged"),
    [
        pytest.param(
            {
                "error": "invalid_client",
                "error_description": "The provided client secret is invalid.",
            },
            "invalid_client",
            id="wrong-secret",
        ),
        pytest.param({"error": "invalid_grant"}, "invalid_grant", id="spent-code"),
        pytest.param({"error": "code=4/abc secret"}, "unrecognised", id="not-an-error-code"),
        pytest.param(None, "unparseable", id="not-json"),
    ],
)
def test_a_google_rejection_logs_only_its_error_code(
    rsa_key: rsa.RSAPrivateKey, body: dict[str, str] | None, logged: str
) -> None:
    """A wrong client secret and a spent code both read as the same 403; the
    log says which, and nothing from the request or Google's description."""

    def handler(_request: httpx.Request) -> httpx.Response:
        if body is None:
            return httpx.Response(401, text="<html>nope</html>")
        return httpx.Response(401, json=body)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with capture_logs() as logs, pytest.raises(PermissionDeniedError):
        _client(rsa_key, http=http).exchange_code("auth-code")

    (entry,) = [e for e in logs if e["event"] == "auth_google_token_rejected"]
    assert entry["error"] == logged
    assert entry["status"] == 401
    assert "auth-code" not in repr(entry) and "secret is invalid" not in repr(entry)


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
    Base.metadata.create_all(engine, tables=[User.__table__, Team.__table__, TeamMember.__table__])
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


def test_upsert_follows_a_changed_google_email(db: Session) -> None:
    upsert_user_from_google(db, _identity())
    user = upsert_user_from_google(db, _identity(email="new@example.com"))
    assert user.email == "new@example.com"


def test_upsert_keeps_the_old_email_when_another_user_holds_the_new_one(db: Session) -> None:
    first = upsert_user_from_google(db, _identity())
    db.add(User(id="user_other", email="taken@example.com", display_name="Other"))
    db.flush()

    user = upsert_user_from_google(db, _identity(email="taken@example.com"))

    assert user.id == first.id
    assert user.email == "a@example.com"
    assert user.last_login_at is not None


def test_upsert_links_an_existing_magic_link_user_by_email(db: Session) -> None:
    existing = User(id="user_pre", email="a@example.com", display_name="Pre")
    db.add(existing)
    db.flush()

    user = upsert_user_from_google(db, _identity())
    assert user.id == "user_pre"
    assert user.google_sub == "sub-1"


# --- an address is compared without case (#552) --------------------------------------


def test_a_row_stored_with_capitals_is_the_same_person_and_not_a_second_account(
    db: Session,
) -> None:
    """The case this closes: a row a magic link (or an older sign-in) stored as
    typed, and Google sending the same mailbox in lower case."""
    db.add(User(id="user_pre", email="Kim.Minsu@Example.com", display_name="Pre"))
    db.flush()

    user = upsert_user_from_google(db, _identity(email="kim.minsu@example.com"))

    assert user.id == "user_pre"
    assert user.google_sub == "sub-1"
    assert db.query(User).count() == 1
    # Found without case, and left as stored: a sign-in does not rewrite an address.
    assert user.email == "Kim.Minsu@Example.com"


def test_the_other_way_round_is_one_person_too(db: Session) -> None:
    db.add(User(id="user_pre", email="kim@example.com", display_name="Pre"))
    db.flush()

    user = upsert_user_from_google(db, _identity(email="Kim@Example.COM"))

    assert user.id == "user_pre"
    assert db.query(User).count() == 1


def test_a_new_account_is_stored_trimmed_and_in_lower_case(db: Session) -> None:
    user = upsert_user_from_google(db, _identity(email="  New.Person@Example.com ", name=None))

    assert user.email == "new.person@example.com"
    assert user.display_name == "new.person@example.com"


def test_a_later_sign_in_does_not_take_a_case_difference_for_a_changed_address(
    db: Session,
) -> None:
    """Found by ``google_sub``, with the address stored in capitals: nothing
    changed at Google, so nothing is written and nobody is warned about."""
    db.add(User(id="user_pre", email="A@Example.com", display_name="Pre", google_sub="sub-1"))
    db.flush()

    with capture_logs() as logs:
        user = upsert_user_from_google(db, _identity(email="a@example.com"))

    assert user.id == "user_pre"
    assert user.email == "A@Example.com"
    assert not [entry for entry in logs if entry["event"] == "auth_google_email_taken"]


def test_a_changed_address_is_stored_in_lower_case(db: Session) -> None:
    upsert_user_from_google(db, _identity())

    user = upsert_user_from_google(db, _identity(email="Moved@Example.com"))

    assert user.email == "moved@example.com"


def test_an_address_another_account_holds_in_another_case_is_still_taken(db: Session) -> None:
    first = upsert_user_from_google(db, _identity())
    db.add(User(id="user_other", email="Taken@Example.com", display_name="Other"))
    db.flush()

    with capture_logs() as logs:
        user = upsert_user_from_google(db, _identity(email="taken@example.com"))

    assert user.id == first.id
    assert user.email == "a@example.com"
    (warning,) = [entry for entry in logs if entry["event"] == "auth_google_email_taken"]
    assert warning["holder_id"] == "user_other"
    # Ids only: an address is personal data and is in no log line.
    assert "example.com" not in repr(logs).lower()


def test_two_rows_that_differ_only_by_case_do_not_break_the_sign_in(db: Session) -> None:
    """The unique constraint lets them exist. The one stored as Google sends the
    address is the one signed in to; with neither exact, the older."""
    old = datetime(2026, 9, 1, tzinfo=UTC)
    db.add(User(id="user_caps", email="Kim@Example.com", display_name="Caps", created_at=old))
    db.add(
        User(
            id="user_lower",
            email="kim@example.com",
            display_name="Lower",
            created_at=old + timedelta(days=1),
        )
    )
    db.flush()

    with capture_logs() as logs:
        exact = upsert_user_from_google(db, _identity(sub="sub-a", email="kim@example.com"))
    assert exact.id == "user_lower"
    (warning,) = [e for e in logs if e["event"] == "auth_email_held_twice_by_case"]
    assert warning["user_ids"] == ["user_caps", "user_lower"]

    neither = upsert_user_from_google(db, _identity(sub="sub-b", email="KIM@example.com"))
    assert neither.id == "user_caps"
    assert db.query(User).count() == 2


def test_the_callback_signs_a_capitalised_row_in_without_making_a_second_account(
    api: tuple[TestClient, dict[str, object]], db: Session
) -> None:
    """Through the route a browser takes, not the function alone."""
    db.add(User(id="user_pre", email="Kim@Example.com", display_name="Pre"))
    db.commit()
    client, holder = api
    holder["identity"] = _identity(email="kim@example.com")

    response = _complete_login(client, holder["state_store"])  # type: ignore[arg-type]

    assert response.status_code == 303
    assert SESSION_COOKIE in response.cookies
    (user,) = db.query(User).all()
    assert (user.id, user.google_sub) == ("user_pre", "sub-1")


# --------------------------------------------------------------------------- #
# /api/auth router
# --------------------------------------------------------------------------- #


class FakeGoogleClient:
    def __init__(self, identity: GoogleIdentity, seen: dict[str, object] | None = None) -> None:
        self.identity = identity
        self.seen = seen if seen is not None else {}

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        self.seen["challenge"] = code_challenge
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}&nonce={nonce}"

    def exchange_code(self, code: str, *, code_verifier: str | None = None) -> str:
        self.seen["verifier"] = code_verifier
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
        holder["identity"],  # type: ignore[arg-type]
        holder,
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


# --------------------------------------------------------------------------- #
# redirect_to: three layers, tested one at a time
#
# End-to-end the three cover for each other, which is the point of having them
# and the reason an end-to-end test cannot say which one is working. Removing
# any single layer left the parametrised callback tests below green. So each
# layer is also checked at its own door.
# --------------------------------------------------------------------------- #

HOSTILE_TARGETS = [
    pytest.param("https://evil.test/phish", id="absolute"),
    pytest.param("//evil.test/phish", id="scheme-relative"),
    pytest.param("/https://evil.test/phish", id="one-slash-then-absolute"),
    pytest.param("/https:evil.test", id="one-slash-then-scheme"),
    pytest.param("/\\evil.test", id="backslash"),
]


@pytest.mark.parametrize("target", HOSTILE_TARGETS)
def test_the_redirect_guard_reduces_a_hostile_target_to_root(target: str) -> None:
    """Layer 1. Four of these five start with a single `/`, which is all the
    guard used to require."""
    assert auth_router_module._safe_redirect_target(target) == "/"


@pytest.mark.parametrize(
    "target", ["/", "/dashboard", "/meetings/mtg_1/actions", "/gap?level=high"]
)
def test_the_redirect_guard_keeps_a_real_screen_path(target: str) -> None:
    assert auth_router_module._safe_redirect_target(target) == target


@pytest.mark.parametrize("target", HOSTILE_TARGETS)
def test_the_url_builder_stays_on_our_origin_even_if_the_guard_let_it_past(
    target: str,
) -> None:
    """Layer 2 and 3, called directly with what layer 1 is supposed to stop.

    This is the test the fix actually needs: `_web_url` used `urljoin`, which
    returns an absolute reference unchanged, so `/https://evil.test/phish`
    became the `Location` after a real sign-in. Going through
    `_safe_redirect_target` first would hide that — as it hid it from the
    end-to-end tests when each layer was removed in turn.
    """
    built = auth_router_module._web_url(target)
    assert built.startswith("http://localhost:3000/")
    assert "evil.test" not in urlsplit(built).netloc


OFFSITE_REDIRECT_TARGETS = [
    pytest.param("https://evil.test/phish", id="absolute"),
    pytest.param("//evil.test/phish", id="scheme-relative"),
    pytest.param("/https://evil.test/phish", id="one-slash-then-absolute"),
    pytest.param("/https:evil.test", id="one-slash-then-scheme"),
    pytest.param("/\\evil.test", id="backslash"),
]
"""Every shape that has to end up back on our own origin.

The first was the only one tested. The next four all start with a single `/`,
which is what `_safe_redirect_target` was checking for, and `_web_url` stripped
that `/` before handing the rest to `urljoin` — which returns an absolute
reference unchanged. So a link on our domain, a real Google sign-in, and then
`303` to somebody else's site (@PARKJAEKYUNG0525 on #425).
"""


@pytest.mark.parametrize("target", OFFSITE_REDIRECT_TARGETS)
def test_an_offsite_redirect_target_never_leaves_our_origin(
    api: tuple[TestClient, dict[str, object]], target: str
) -> None:
    """Driven to the callback, because that is where the `Location` is written.

    `test_start_refuses_an_offsite_redirect_target` checks what was stored,
    which is necessary and was not enough: the stored value was already `/` for
    the absolute case and something that *looked* like a path for the rest.
    What matters is the header the browser is handed at the end.
    """
    client, holder = api
    start = client.get(f"/api/auth/google/start?redirect_to={target}")
    state = start.headers["location"].split("state=", 1)[1].split("&", 1)[0]

    response = client.get(f"/api/auth/google/callback?state={state}&code=abc")

    assert response.status_code == 303
    assert response.headers["location"].startswith("http://localhost:3000/")


def test_a_legitimate_redirect_target_still_arrives(
    api: tuple[TestClient, dict[str, object]],
) -> None:
    """The guard has to let the product through, which is the other half of it."""
    client, _ = api
    start = client.get("/api/auth/google/start?redirect_to=/meetings/mtg_1/actions")
    state = start.headers["location"].split("state=", 1)[1].split("&", 1)[0]

    response = client.get(f"/api/auth/google/callback?state={state}&code=abc")

    assert response.headers["location"] == "http://localhost:3000/meetings/mtg_1/actions"


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


def test_sign_in_sends_a_challenge_and_exchanges_with_its_verifier(
    api: tuple[TestClient, dict[str, object]],
) -> None:
    """#704: the verifier never leaves the server until the exchange, and it
    is the one the challenge was made from."""
    client, holder = api
    _complete_login(client, holder["state_store"])  # type: ignore[arg-type]

    verifier = holder["verifier"]
    assert isinstance(verifier, str)
    digest = hashlib.sha256(verifier.encode()).digest()
    assert holder["challenge"] == base64.urlsafe_b64encode(digest).decode().rstrip("=")


def test_start_binds_the_state_to_this_browser(
    api: tuple[TestClient, dict[str, object]],
) -> None:
    client, _ = api
    response = client.get("/api/auth/google/start")

    state = response.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    assert response.cookies[STATE_COOKIE] == state
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/api/auth/google/callback" in set_cookie


def test_callback_from_another_browser_is_refused(
    api: tuple[TestClient, dict[str, object]], db: Session
) -> None:
    """Login CSRF: the attacker starts the flow, consents with their own Google
    account, and sends the callback URL to a victim instead of following it."""
    attacker, holder = api
    start = attacker.get("/api/auth/google/start")
    state = start.headers["location"].split("state=", 1)[1].split("&", 1)[0]

    victim = TestClient(attacker.app, follow_redirects=False)
    response = victim.get(f"/api/auth/google/callback?state={state}&code=abc")

    assert response.status_code == 403
    assert SESSION_COOKIE not in response.cookies
    assert db.query(User).count() == 0
    store = holder["state_store"]
    assert len(store._entries) == 1  # type: ignore[attr-defined]  # not spent


def test_callback_refuses_a_state_cookie_that_does_not_match(
    api: tuple[TestClient, dict[str, object]],
) -> None:
    client, _ = api
    start = client.get("/api/auth/google/start")
    state = start.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    client.cookies.set(STATE_COOKIE, "someone-elses-state", path="/api/auth/google/callback")

    response = client.get(f"/api/auth/google/callback?state={state}&code=abc")
    assert response.status_code == 403
    assert SESSION_COOKIE not in response.cookies


def test_callback_refuses_a_non_ascii_state_without_a_500(
    api: tuple[TestClient, dict[str, object]],
) -> None:
    client, _ = api
    client.cookies.set(STATE_COOKIE, "abc", path="/api/auth/google/callback")
    response = client.get("/api/auth/google/callback?state=%ED%95%9C&code=abc")
    assert response.status_code == 403


def test_callback_clears_the_state_cookie_either_way(
    api: tuple[TestClient, dict[str, object]],
) -> None:
    client, holder = api
    signed_in = _complete_login(client, holder["state_store"])  # type: ignore[arg-type]
    holder["identity"] = _identity(email_verified=False)
    refused = _complete_login(client, holder["state_store"])  # type: ignore[arg-type]

    assert (signed_in.status_code, refused.status_code) == (303, 403)
    for response in (signed_in, refused):
        cleared = [c for c in response.headers.get_list("set-cookie") if c.startswith(STATE_COOKIE)]
        assert cleared and "max-age=0" in cleared[0].lower()


def test_callback_rejects_an_unknown_state(
    api: tuple[TestClient, dict[str, object]],
) -> None:
    client, _ = api
    # The cookie matches, so this reaches the store and fails there.
    client.cookies.set(STATE_COOKIE, "forged", path="/api/auth/google/callback")
    response = client.get("/api/auth/google/callback?state=forged&code=abc")
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "sign-in state is unknown or has expired"


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


def test_me_lists_the_teams_the_person_belongs_to(
    api: tuple[TestClient, dict[str, object]], db: Session
) -> None:
    """S28 settings (#496) picks a team's integrations from these -- there is no
    meeting to name the team there.

    **First joined, first listed** (#742). The person made ``B팀`` and later
    accepted an invitation to ``A팀``, whose name sorts earlier. The screens
    that take ``teams[0]`` as the default must still get the team the person
    started in, not the one that invited them."""
    client, _ = api
    user = upsert_user_from_google(db, _identity())
    db.add_all(
        [
            Team(id="team_b", name="B팀"),
            Team(id="team_a", name="A팀"),
            Team(id="team_x", name="남의 팀"),
        ]
    )
    db.flush()
    # One at a time: the order of the rows is the order of joining.
    db.add(TeamMember(team_id="team_b", user_id=user.id))
    db.flush()
    db.add(TeamMember(team_id="team_a", user_id=user.id))
    db.commit()
    client.cookies.set(SESSION_COOKIE, issue_token(user.id))

    teams = client.get("/api/auth/me").json()["teams"]

    assert teams == [{"id": "team_b", "name": "B팀"}, {"id": "team_a", "name": "A팀"}]


def test_me_lists_pinned_teams_first_in_the_order_they_were_pinned(
    api: tuple[TestClient, dict[str, object]], db: Session
) -> None:
    """A pin (module A writes ``team_members.pinned_at``) puts a team before
    the ones merely joined, whenever it was joined. The assistant and S28 take
    ``teams[0]``, so this is how a person chooses their default."""
    client, _ = api
    user = upsert_user_from_google(db, _identity())
    for team_id in ("team_1", "team_2", "team_3"):
        db.add(Team(id=team_id, name=team_id))
        db.flush()
        db.add(TeamMember(team_id=team_id, user_id=user.id))
        db.flush()
    first = datetime(2026, 10, 2, tzinfo=UTC)
    for team_id, when in (("team_3", first), ("team_2", first + timedelta(minutes=1))):
        db.execute(
            update(TeamMember)
            .where(TeamMember.team_id == team_id, TeamMember.user_id == user.id)
            .values(pinned_at=when)
        )
    db.commit()
    client.cookies.set(SESSION_COOKIE, issue_token(user.id))

    teams = client.get("/api/auth/me").json()["teams"]

    assert [team["id"] for team in teams] == ["team_3", "team_2", "team_1"]


def test_providers_reports_whether_google_is_configured(
    api: tuple[TestClient, dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "")
    assert client.get("/api/auth/providers").json() == {"google": False}

    monkeypatch.setattr(settings, "google_client_id", "id")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(settings, "google_redirect_uri", "http://localhost:3000/cb")
    assert client.get("/api/auth/providers").json() == {"google": True}


def test_logout_clears_the_cookie(api: tuple[TestClient, dict[str, object]]) -> None:
    client, _ = api
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    assert 'autune_session=""' in response.headers.get("set-cookie", "") or (
        "autune_session=;" in response.headers.get("set-cookie", "")
    )


def test_verify_request_holds_for_a_token_without_an_email(rsa_key: rsa.RSAPrivateKey) -> None:
    """The calendar consent has no ``email`` scope (#452 review)."""
    token = _id_token(rsa_key, email=None, email_verified=None)
    claims = _client(rsa_key).verify_request(token, nonce="the-nonce")
    assert claims["sub"] == "google-sub-1"
    with pytest.raises(PermissionDeniedError, match="no email"):
        _client(rsa_key).verify(token, nonce="the-nonce")


def test_verify_request_still_checks_the_nonce(rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(PermissionDeniedError, match="nonce"):
        _client(rsa_key).verify_request(_id_token(rsa_key, email=None), nonce="different")
