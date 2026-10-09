"""The routes under ``/api/auth`` commit before they answer (#1041).

``get_session`` commits after the response has been sent; ``SessionDep`` commits
as the route returns. What the dependency does is tested in
``test_committed_session.py``. This file holds ``auth_router`` to it: no route
takes ``get_session`` directly, and two of them are run against the real
``get_session`` -- a redirect and a JSON answer -- with the database's commit
watched against the response's first message.

It matters most at sign-in: the callback's redirect carries the session cookie,
and the page the browser lands on asks ``/me`` at once. A person stored after
that redirect has left is a person ``/me`` does not know yet.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from autune_core import SESSION_COOKIE, UserConsent, committed_session, db, get_session, issue_token
from autune_core.auth_router import router as auth_router
from autune_core.db import Base
from autune_core.entities import Team, TeamMember, User
from autune_core.errors import AutuneError
from autune_core.oauth.google import GoogleIdentity, get_google_client
from autune_core.oauth.state import InMemoryStateStore, get_state_store

ME = "user_me"


def _routes() -> list[APIRoute]:
    return [route for route in auth_router.routes if isinstance(route, APIRoute)]


def _name(route: APIRoute) -> str:
    return f"{sorted(route.methods)[0]} {route.path}"


def test_no_route_takes_the_session_that_commits_after_the_response() -> None:
    late = [
        _name(route)
        for route in _routes()
        if any(dep.call is get_session for dep in route.dependant.dependencies)
    ]

    assert late == []


def test_the_routes_take_the_session_committed_as_they_return() -> None:
    """The check above passes on a router with no session at all; this one says
    the routes are there and that the commit is tied to the route function."""
    taken = [
        dep
        for route in _routes()
        for dep in route.dependant.dependencies
        if dep.call is committed_session
    ]

    assert len(taken) >= 25
    assert {dep.scope for dep in taken} == {"function"}


# --------------------------------------------------------------------------- #
# Two routes, run: the real ``get_session``, nothing of the database overridden
# --------------------------------------------------------------------------- #


class _ResponseOrder:
    """Records the moment the response starts, among the commits around it."""

    def __init__(self, app: Any, seen: list[str]) -> None:
        self.app = app
        self.seen = seen

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        async def _send(message: Any) -> None:
            if message["type"] == "http.response.start":
                self.seen.append("response")
            await send(message)

        await self.app(scope, receive, _send)


class _Google:
    """Stands in for Google: one identity, whatever the code."""

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}&nonce={nonce}"

    def exchange_code(self, code: str, *, code_verifier: str | None = None) -> str:
        return "fake-id-token"

    def verify(self, id_token: str, *, nonce: str) -> GoogleIdentity:
        return GoogleIdentity(
            sub="sub-1", email="a@example.com", email_verified=True, name="A", picture=None
        )


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.Engine]:
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, Team.__table__, TeamMember.__table__, UserConsent.__table__],
    )
    monkeypatch.setattr(
        db, "get_sessionmaker", lambda: sessionmaker(bind=engine, expire_on_commit=False)
    )
    yield engine
    engine.dispose()


@pytest.fixture
def seen(engine: sa.Engine) -> list[str]:
    """What happened, in order -- listened for only once the fixture rows are in."""
    with sessionmaker(bind=engine)() as session:
        session.add(User(id=ME, email="me@example.com", display_name="Me"))
        session.commit()
    seen: list[str] = []
    sa.event.listen(engine, "commit", lambda _: seen.append("commit"))
    return seen


@pytest.fixture
def client(seen: list[str]) -> TestClient:
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")

    @app.exception_handler(AutuneError)
    def _handle(_req: object, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    store = InMemoryStateStore()
    app.dependency_overrides[get_state_store] = lambda: store
    app.dependency_overrides[get_google_client] = lambda: _Google()
    app.add_middleware(_ResponseOrder, seen=seen)
    return TestClient(app, follow_redirects=False)


def _people(engine: sa.Engine) -> list[str]:
    with engine.connect() as connection:
        return list(connection.scalars(sa.select(User.google_sub).where(User.id != ME)))


def test_the_person_is_stored_before_the_sign_in_redirect_leaves(
    client: TestClient, engine: sa.Engine, seen: list[str]
) -> None:
    start = client.get("/api/auth/google/start?redirect_to=/dashboard")
    state = start.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    seen.clear()  # the start wrote nothing; what follows is the callback alone

    response = client.get(f"/api/auth/google/callback?state={state}&code=abc")

    assert response.status_code == 303
    assert SESSION_COOKIE in response.cookies
    assert seen == ["commit", "response"]
    assert _people(engine) == ["sub-1"]


def test_an_agreement_is_stored_before_its_answer_leaves(
    client: TestClient, engine: sa.Engine, seen: list[str]
) -> None:
    client.cookies.set(SESSION_COOKIE, issue_token(ME))

    response = client.post(
        "/api/auth/consents", json={"consents": [{"document": "terms", "version": "2026-10-02"}]}
    )

    assert response.status_code == 200
    assert seen == ["commit", "response"]
    with engine.connect() as connection:
        assert list(connection.scalars(sa.select(UserConsent.user_id))) == [ME]
