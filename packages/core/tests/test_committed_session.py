"""``SessionDep`` commits before the response leaves; ``get_session`` alone does not.

``get_session`` commits after its ``yield``, and FastAPI runs that part after
the response has been sent (#1041). A client that acts on a 2xx at once could
read before the write existed, and a commit that failed there failed after the
client had been told it worked.

These run the real ``get_session`` against an in-memory database, with nothing
overridden, and watch two things in order: the database's commit and the
response's first byte.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any

import pytest
import sqlalchemy as sa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from autune_core import SessionDep, db, get_session

# A table of its own, off ``Base``: nothing here belongs in the shared metadata.
_metadata = sa.MetaData()
notes = sa.Table("notes", _metadata, sa.Column("id", sa.Integer, primary_key=True))


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


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.Engine]:
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    _metadata.create_all(engine)
    monkeypatch.setattr(
        db, "get_sessionmaker", lambda: sessionmaker(bind=engine, expire_on_commit=False)
    )
    yield engine
    engine.dispose()


@pytest.fixture
def seen(engine: sa.Engine) -> list[str]:
    seen: list[str] = []
    sa.event.listen(engine, "commit", lambda _: seen.append("commit"))
    return seen


def _client(app: FastAPI, seen: list[str]) -> TestClient:
    app.add_middleware(_ResponseOrder, seen=seen)
    return TestClient(app, raise_server_exceptions=False)


def _stored(engine: sa.Engine) -> list[int]:
    with engine.connect() as connection:
        return list(connection.scalars(sa.select(notes.c.id)))


def test_the_write_is_committed_before_the_response_starts(
    engine: sa.Engine, seen: list[str]
) -> None:
    app = FastAPI()

    @app.post("/notes", status_code=201)
    def create(session: SessionDep) -> dict[str, int]:
        session.execute(sa.insert(notes).values(id=1))
        return {"id": 1}

    response = _client(app, seen).post("/notes")

    assert response.status_code == 201
    assert seen == ["commit", "response"]
    assert _stored(engine) == [1]


def test_get_session_alone_commits_after_the_response_has_started(
    engine: sa.Engine, seen: list[str]
) -> None:
    """The gap itself, and the check that the probe above can tell the two apart.

    If this one fails, FastAPI has moved a request-scoped teardown back before
    the response, and ``SessionDep`` is no longer what keeps the order.
    """
    app = FastAPI()

    @app.post("/notes", status_code=201)
    def create(session: Annotated[Session, Depends(get_session)]) -> dict[str, int]:
        session.execute(sa.insert(notes).values(id=1))
        return {"id": 1}

    response = _client(app, seen).post("/notes")

    assert response.status_code == 201
    assert seen == ["response", "commit"]


def test_a_route_that_raises_commits_nothing(engine: sa.Engine, seen: list[str]) -> None:
    app = FastAPI()

    @app.post("/notes")
    def create(session: SessionDep) -> dict[str, int]:
        session.execute(sa.insert(notes).values(id=1))
        raise RuntimeError("after the write")

    response = _client(app, seen).post("/notes")

    assert response.status_code == 500
    assert "commit" not in seen
    assert _stored(engine) == []


def test_a_commit_that_fails_is_an_error_to_the_client_not_a_success(
    engine: sa.Engine, seen: list[str]
) -> None:
    """The route returns, the commit fails: the client must not be told 201."""
    app = FastAPI()

    @app.post("/notes", status_code=201)
    def create(session: SessionDep) -> dict[str, int]:
        session.execute(sa.insert(notes).values(id=1))
        return {"id": 1}

    def _refuse(_: Session) -> None:
        raise sa.exc.OperationalError("COMMIT", {}, Exception("the database went away"))

    sa.event.listen(Session, "before_commit", _refuse)
    try:
        response = _client(app, seen).post("/notes")
    finally:
        sa.event.remove(Session, "before_commit", _refuse)

    assert response.status_code == 500
    assert _stored(engine) == []


def test_the_route_and_its_other_dependencies_share_one_session(
    engine: sa.Engine, seen: list[str]
) -> None:
    """``CurrentUser`` takes ``get_session``; the route must see the same
    session, or the person it read and the row it writes are two transactions."""
    sessions: list[Session] = []

    def reader(session: Annotated[Session, Depends(get_session)]) -> None:
        sessions.append(session)

    app = FastAPI()

    @app.post("/notes", status_code=201)
    def create(session: SessionDep, _: None = Depends(reader)) -> dict[str, int]:
        sessions.append(session)
        session.execute(sa.insert(notes).values(id=1))
        return {"id": 1}

    response = _client(app, seen).post("/notes")

    assert response.status_code == 201
    assert len(sessions) == 2
    assert sessions[0] is sessions[1]
    assert seen.count("commit") == 1
