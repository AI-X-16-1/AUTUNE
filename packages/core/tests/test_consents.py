"""What a person agreed to: recorded per document and version, theirs only, and
never a gate -- without a Postgres or a Redis."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from autune_core import SESSION_COOKIE, UserConsent, issue_token
from autune_core import consents as consents_module
from autune_core.auth_router import router as auth_router
from autune_core.consents import DOCUMENTS, MAX_PER_REQUEST, consents_of, record_consents
from autune_core.db import Base, get_session
from autune_core.entities import User
from autune_core.errors import AutuneError, ValidationError

ME = "user_me"
OTHER = "user_other"
TABLE = "user_consents"


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[User.__table__, UserConsent.__table__])
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

    app.dependency_overrides[get_session] = lambda: db
    return app


def signed_in(app: FastAPI, user_id: str = ME) -> TestClient:
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, issue_token(user_id))
    return client


def agreed(body: dict[str, Any]) -> list[tuple[str, str]]:
    return [(c["document"], c["version"]) for c in body["consents"]]


TERMS = {"document": "terms", "version": "2026-10-02"}
PRIVACY = {"document": "privacy", "version": "2026-10-02"}


# --- the routes ---------------------------------------------------------------------


def test_nobody_has_agreed_to_anything_at_first(app: FastAPI) -> None:
    assert signed_in(app).get("/api/auth/consents").json() == {"consents": []}


def test_agreeing_is_recorded_with_its_time_and_read_back(app: FastAPI) -> None:
    client = signed_in(app)

    posted = client.post("/api/auth/consents", json={"consents": [TERMS, PRIVACY]})

    assert posted.status_code == 200
    assert agreed(posted.json()) == [("terms", "2026-10-02"), ("privacy", "2026-10-02")]
    assert all(c["agreed_at"] for c in posted.json()["consents"])
    assert agreed(client.get("/api/auth/consents").json()) == agreed(posted.json())


def test_agreeing_again_keeps_the_first_agreement(app: FastAPI, db: Session) -> None:
    """The record says when a person first agreed to that version, not when
    they last pressed the button."""
    client = signed_in(app)
    first = client.post("/api/auth/consents", json={"consents": [TERMS]}).json()

    again = client.post("/api/auth/consents", json={"consents": [TERMS, TERMS]}).json()

    assert again == first
    assert len(consents_of(db, ME)) == 1


def test_a_changed_document_is_a_version_nobody_has_agreed_to_yet(app: FastAPI) -> None:
    client = signed_in(app)
    client.post("/api/auth/consents", json={"consents": [TERMS]})

    newer = {"document": "terms", "version": "2026-11-01"}
    body = client.post("/api/auth/consents", json={"consents": [newer]}).json()

    assert agreed(body) == [("terms", "2026-10-02"), ("terms", "2026-11-01")]


def test_what_one_person_agreed_to_is_theirs_alone(app: FastAPI) -> None:
    """There is no parameter that names another person, and a second person's
    list does not hold the first's."""
    signed_in(app).post("/api/auth/consents", json={"consents": [TERMS]})

    assert signed_in(app, OTHER).get("/api/auth/consents").json() == {"consents": []}
    assert signed_in(app, OTHER).get(f"/api/auth/consents?user_id={ME}").json() == {"consents": []}


def test_agreeing_needs_a_signed_in_person(app: FastAPI) -> None:
    anonymous = TestClient(app)

    assert anonymous.get("/api/auth/consents").status_code in (401, 403)
    assert anonymous.post("/api/auth/consents", json={"consents": [TERMS]}).status_code in (
        401,
        403,
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"document": "Terms Of Service", "version": "1"},
        # Consents a person must be able to withdraw are not recorded here.
        {"document": "voice_features", "version": "1"},
        {"document": "overseas_transfer", "version": "1"},
        {"document": "terms", "version": ""},
        {"document": "terms", "version": "1; drop table"},
        {"document": "", "version": "1"},
        {"document": "t" * 65, "version": "1"},
    ],
)
def test_a_name_or_version_that_is_not_one_is_refused_and_nothing_is_written(
    app: FastAPI, db: Session, bad: dict[str, str]
) -> None:
    response = signed_in(app).post("/api/auth/consents", json={"consents": [TERMS, bad]})

    assert response.status_code == 422
    assert consents_of(db, ME) == []


def test_two_requests_at_once_are_one_agreement_not_an_error(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A double click, or two tabs: both read no row, both insert. The second
    to arrive meets the unique constraint, and what it wanted is already
    true. The other document in the same request is still recorded.

    The race is staged by handing ``record_consents`` the stale read the
    slower request would have made."""
    record_consents(db, ME, [("terms", "2026-10-02")])
    db.commit()
    first = consents_of(db, ME)
    real = consents_module.consents_of
    reads = iter([[]])  # the first read: before the other request committed
    monkeypatch.setattr(
        consents_module, "consents_of", lambda s, u: next(reads, None) or real(s, u)
    )

    recorded = record_consents(db, ME, [("terms", "2026-10-02"), ("privacy", "2026-10-02")])
    db.commit()

    assert [(c.document, c.version) for c in recorded] == [
        ("terms", "2026-10-02"),
        ("privacy", "2026-10-02"),
    ]
    assert recorded[0].agreed_at == first[0].agreed_at  # the first agreement stands


def test_one_request_cannot_write_without_bound(db: Session) -> None:
    many = [(f"doc_{i}", "1") for i in range(MAX_PER_REQUEST + 1)]

    with pytest.raises(ValidationError):
        record_consents(db, ME, many)


# --- the table ----------------------------------------------------------------------


def test_a_consent_goes_with_the_account() -> None:
    """It is the person's record: the foreign key is declared to delete it with
    the account, and nothing is kept behind as proof. This reads the
    declaration only -- SQLite does not enforce it. The delete itself was run
    on a real Postgres while this was written and reviewed (#715)."""
    fk = next(iter(Base.metadata.tables[TABLE].c.user_id.foreign_keys))
    assert fk.ondelete == "CASCADE"


def test_the_table_holds_the_terms_and_the_privacy_policy_and_nothing_else() -> None:
    """mkkim68, review of #715: a row here can only say "agreed", so nothing
    that needs a withdrawal goes in it. The module's list and the database's
    constraint say the same two names."""
    assert DOCUMENTS == ("terms", "privacy")
    checks = {
        c.name: str(c.sqltext)
        for c in Base.metadata.tables[TABLE].constraints
        if c.name == "ck_user_consents_document"
    }
    assert checks == {"ck_user_consents_document": "document IN ('terms','privacy')"}


def test_one_row_per_person_document_and_version() -> None:
    names = {c.name for c in Base.metadata.tables[TABLE].constraints}
    assert "uq_user_consents_user_document_version" in names


def test_table_is_shared_not_module_owned() -> None:
    assert not TABLE.startswith(("aud_", "ext_", "gap_", "ctx_", "intel_", "agent_"))
