"""Signing out ends the person's sessions on the server, on every device -- and
nobody else's, and nobody's by accident. Without a Postgres or a Redis."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from autune_core import SESSION_COOKIE, issue_token
from autune_core.auth import ALGORITHM, current_user, end_sessions, user_for_token
from autune_core.auth_router import router as auth_router
from autune_core.db import Base, get_session
from autune_core.entities import Team, TeamMember, User
from autune_core.errors import AutuneError, NotFoundError, PermissionDeniedError
from autune_core.settings import get_settings

ME = "user_me"
OTHER = "user_other"


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[User.__table__, Team.__table__, TeamMember.__table__])
    session = sessionmaker(bind=engine)()
    session.add(User(id=ME, email="me@example.com", display_name="Me"))
    session.add(User(id=OTHER, email="o@example.com", display_name="Other"))
    session.commit()
    return session


@pytest.fixture
def app(db: Session) -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")

    @app.exception_handler(AutuneError)
    def _handle(_req: object, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    def session() -> Session:
        # What ``get_session`` does on the way out of a request.
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_session] = session
    return app


def browser(app: FastAPI, token: str) -> TestClient:
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    return client


def me(client: TestClient) -> int:
    return client.get("/api/auth/me").status_code


def test_a_signed_out_session_is_refused_by_the_server(app: FastAPI) -> None:
    """The point of the change: the token the cookie held is not good any more,
    wherever a copy of it is."""
    token = issue_token(ME)
    client = browser(app, token)
    assert me(client) == 200

    assert client.post("/api/auth/logout").status_code == 204

    assert me(browser(app, token)) == 403


def test_signing_out_in_one_browser_signs_out_every_other(app: FastAPI) -> None:
    """Decided with the user: one value per person, so every device."""
    laptop, phone = issue_token(ME), issue_token(ME)

    browser(app, laptop).post("/api/auth/logout")

    assert me(browser(app, phone)) == 403


def test_a_bearer_token_follows_the_same_rule(app: FastAPI) -> None:
    """A developer token is the same kind of token; it ends with the rest and
    can end the rest."""
    cookie, bearer = issue_token(ME), issue_token(ME)
    scripted = TestClient(app, headers={"authorization": f"Bearer {bearer}"})
    assert scripted.get("/api/auth/me").status_code == 200

    scripted.post("/api/auth/logout")

    assert scripted.get("/api/auth/me").status_code == 403
    assert me(browser(app, cookie)) == 403


def later_token(user_id: str) -> str:
    """A token from a later reading of the clock than whatever came before.
    Back to back, two readings can be the same value on a coarse clock, and
    a token issued at the instant of a sign-out is ended with it."""
    time.sleep(0.05)
    return issue_token(user_id)


def token_issued_at(moment: datetime) -> str:
    return jwt.encode(
        {"sub": ME, "iat": moment.timestamp(), "exp": moment + timedelta(days=7)},
        get_settings().secret_key,
        algorithm=ALGORITHM,
    )


def test_signing_in_again_after_signing_out_works(app: FastAPI) -> None:
    browser(app, issue_token(ME)).post("/api/auth/logout")

    assert me(browser(app, later_token(ME))) == 200


def test_the_line_is_drawn_at_the_instant_of_the_sign_out(app: FastAPI, db: Session) -> None:
    """Within the same second: ``iat`` is kept to the microsecond, so a token
    from a millisecond later is good and one from a millisecond earlier is
    not. One issued at the very instant is ended too -- it cannot be told
    from one issued just before, and two readings of a coarse clock are
    often the same value."""
    moment = datetime.now(UTC).replace(microsecond=500_000) - timedelta(seconds=5)
    end_sessions(db.get(User, ME), now=moment)
    db.commit()
    tick = timedelta(milliseconds=1)

    assert me(browser(app, token_issued_at(moment - tick))) == 403
    assert me(browser(app, token_issued_at(moment))) == 403
    assert me(browser(app, token_issued_at(moment + tick))) == 200


def test_nobody_else_is_signed_out(app: FastAPI) -> None:
    theirs = issue_token(OTHER)

    browser(app, issue_token(ME)).post("/api/auth/logout")

    assert me(browser(app, theirs)) == 200


def test_the_cookie_is_cleared_as_before(app: FastAPI) -> None:
    response = browser(app, issue_token(ME)).post("/api/auth/logout")

    cleared = response.headers["set-cookie"]
    assert "autune_session=" in cleared
    assert "Max-Age=0" in cleared
    # With the attributes it was set with (mkkim68, review of #727).
    assert "HttpOnly" in cleared and "samesite=lax" in cleared.lower()


@pytest.mark.parametrize("cookie", [None, "not-a-token"])
def test_signing_out_never_fails_and_ends_nothing_without_a_session(
    app: FastAPI, db: Session, cookie: str | None
) -> None:
    client = TestClient(app)
    if cookie is not None:
        client.cookies.set(SESSION_COOKIE, cookie)

    assert client.post("/api/auth/logout").status_code == 204

    assert db.get(User, ME).sessions_valid_from is None
    assert db.get(User, OTHER).sessions_valid_from is None


def test_a_token_already_signed_out_cannot_move_the_moment(app: FastAPI, db: Session) -> None:
    """A copy of an ended token must not be able to sign the person out again
    later, ending a session they started since."""
    old = issue_token(ME)
    browser(app, old).post("/api/auth/logout")
    first = db.get(User, ME).sessions_valid_from
    fresh = later_token(ME)

    assert browser(app, old).post("/api/auth/logout").status_code == 204

    assert db.get(User, ME).sessions_valid_from == first
    assert me(browser(app, fresh)) == 200


def test_deploying_this_signs_nobody_out(app: FastAPI) -> None:
    """A token from before the change has a whole-second ``iat`` and its owner
    has never signed out: nothing to compare with, so it stays good."""
    before = datetime.now(UTC) - timedelta(hours=3)
    old_style = jwt.encode(
        {"sub": ME, "iat": int(before.timestamp()), "exp": before + timedelta(days=7)},
        get_settings().secret_key,
        algorithm=ALGORITHM,
    )

    assert me(browser(app, old_style)) == 200


def test_an_older_tokens_whole_second_errs_towards_signed_out(app: FastAPI, db: Session) -> None:
    """Issued half a second before the sign-out, recorded as the second's
    start: it is before the sign-out either way."""
    now = datetime.now(UTC).replace(microsecond=500_000)
    old_style = jwt.encode(
        {"sub": ME, "iat": int(now.timestamp()), "exp": now + timedelta(days=7)},
        get_settings().secret_key,
        algorithm=ALGORITHM,
    )
    end_sessions(db.get(User, ME), now=now + timedelta(milliseconds=300))
    db.commit()

    assert me(browser(app, old_style)) == 403


def test_user_for_token_is_the_check_and_current_user_is_only_its_dependency(
    db: Session,
) -> None:
    """Review of #727: the sign-out check lived in ``current_user`` alone, and
    module A's live socket, which read the token itself, never made it. A
    caller that cannot use the dependency calls this, and gets the same
    answer the routes get."""
    before, user = issue_token(ME), db.get(User, ME)
    assert user_for_token(db, before).id == ME

    end_sessions(user)
    db.commit()

    for check in (user_for_token, lambda s, t: current_user(t, s)):
        with pytest.raises(PermissionDeniedError, match="signed out"):
            check(db, before)
    assert user_for_token(db, later_token(ME)).id == ME


def test_a_token_whose_person_is_gone_is_the_one_not_found(db: Session) -> None:
    """The only ``NotFoundError`` the helper raises, so a caller that must not
    answer "not found" for it can catch exactly that."""
    with pytest.raises(NotFoundError):
        user_for_token(db, issue_token("user_nobody"))
    with pytest.raises(PermissionDeniedError):
        user_for_token(db, "not-a-token")


def test_the_moment_never_moves_backwards(db: Session) -> None:
    user = db.get(User, ME)
    late = datetime.now(UTC)
    end_sessions(user, now=late)

    end_sessions(user, now=late - timedelta(minutes=5))

    assert user.sessions_valid_from == late


def test_a_token_with_no_issue_time_is_before_any_sign_out(app: FastAPI, db: Session) -> None:
    no_iat = jwt.encode(
        {"sub": ME, "exp": datetime.now(UTC) + timedelta(days=1)},
        get_settings().secret_key,
        algorithm=ALGORITHM,
    )
    assert me(browser(app, no_iat)) == 200  # never signed out: nothing to compare with

    end_sessions(db.get(User, ME))
    db.commit()

    assert me(browser(app, no_iat)) == 403
