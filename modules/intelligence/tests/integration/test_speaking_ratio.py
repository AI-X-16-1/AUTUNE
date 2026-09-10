"""GET /me/speaking-ratio/{meeting_id} and the service behind it.

The route serves exactly one person their own share of one meeting. There is no
subject in the path — ``/me`` plus the authenticated user *is* the
authorisation, and there is no variant that names someone else. Nothing is
persisted. See docs/architecture/privacy.md section 3.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_core import AutuneError, User, get_session
from autune_core.auth import current_user
from autune_intelligence import service
from autune_intelligence.router import router


@pytest.fixture
def app_for(db_session: Session):
    """Build a client that authenticates as ``user_id`` (or nobody)."""

    def _build(user_id: str | None) -> TestClient:
        app = FastAPI()

        @app.exception_handler(AutuneError)
        async def _render(_: Request, exc: AutuneError) -> JSONResponse:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        app.include_router(router, prefix="/api/intelligence")
        app.dependency_overrides[get_session] = lambda: db_session
        if user_id is not None:
            app.dependency_overrides[current_user] = lambda: db_session.get(User, user_id)
        return TestClient(app)

    return _build


def _user(session: Session, name: str) -> str:
    row = User(email=f"{name}@example.com", display_name=name)
    session.add(row)
    session.flush()
    return row.id


def _participant(
    session: Session, meeting_id: str, *, user_id: str | None, label: str, consented: bool = True
) -> str:
    from autune_core import Participant

    row = Participant(
        meeting_id=meeting_id, user_id=user_id, speaker_label=label, consented=consented
    )
    session.add(row)
    session.flush()
    return row.id


def _utter(
    session: Session, meeting_id: str, participant_id: str | None, start: float, end: float
) -> None:
    from autune_core import Utterance

    session.add(
        Utterance(
            meeting_id=meeting_id,
            participant_id=participant_id,
            speaker_label="s",
            start_sec=start,
            end_sec=end,
            text="[말씀]",
        )
    )


# --- endpoint ---------------------------------------------------------------


def test_returns_the_requesters_own_ratio(app_for, db_session: Session, meeting: str) -> None:
    alice = _user(db_session, "alice")
    bob = _user(db_session, "bob")
    p_alice = _participant(db_session, meeting, user_id=alice, label="Alice")
    p_bob = _participant(db_session, meeting, user_id=bob, label="Bob")
    _utter(db_session, meeting, p_alice, 0.0, 30.0)
    _utter(db_session, meeting, p_bob, 30.0, 40.0)
    db_session.flush()

    body = app_for(alice).get(f"/api/intelligence/me/speaking-ratio/{meeting}").json()

    assert body["ratio"] == pytest.approx(0.75)
    assert body["participant_count"] == 2
    assert body["stored"] is False


def test_a_silent_participant_gets_zero_not_a_404(
    app_for, db_session: Session, meeting: str
) -> None:
    alice = _user(db_session, "alice")
    bob = _user(db_session, "bob")
    p_alice = _participant(db_session, meeting, user_id=alice, label="Alice")
    _participant(db_session, meeting, user_id=bob, label="Bob")
    _utter(db_session, meeting, p_alice, 0.0, 30.0)
    db_session.flush()

    body = app_for(bob).get(f"/api/intelligence/me/speaking-ratio/{meeting}").json()

    assert body["ratio"] == 0.0


def test_404_when_the_requester_was_not_in_the_meeting(
    app_for, db_session: Session, meeting: str
) -> None:
    outsider = _user(db_session, "outsider")
    alice = _user(db_session, "alice")
    p_alice = _participant(db_session, meeting, user_id=alice, label="Alice")
    _utter(db_session, meeting, p_alice, 0.0, 30.0)
    db_session.flush()

    response = app_for(outsider).get(f"/api/intelligence/me/speaking-ratio/{meeting}")

    assert response.status_code == 404


def test_the_route_requires_authentication(app_for, meeting: str) -> None:
    response = app_for(None).get(f"/api/intelligence/me/speaking-ratio/{meeting}")

    assert response.status_code in (401, 403)


def test_there_is_no_route_that_names_another_user(
    app_for, db_session: Session, meeting: str
) -> None:
    alice = _user(db_session, "alice")
    db_session.flush()

    # a subject in the path is simply not routed
    assert (
        app_for(alice).get(f"/api/intelligence/me/speaking-ratio/{meeting}/{alice}").status_code
        == 404
    )


# --- service edge cases ---------------------------------------------------


def test_compute_speaking_shares_puts_unattributed_speech_in_the_denominator(
    db_session: Session, meeting: str
) -> None:
    alice = _user(db_session, "alice")
    p_alice = _participant(db_session, meeting, user_id=alice, label="Alice")
    _utter(db_session, meeting, p_alice, 0.0, 30.0)
    _utter(db_session, meeting, None, 30.0, 60.0)
    db_session.flush()

    shares = service.compute_speaking_shares(db_session, meeting)

    assert len(shares) == 1
    assert shares[0].ratio == pytest.approx(0.5)
