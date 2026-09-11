"""GET /me/speaking-ratio/{meeting_id} and the service behind it.

The route serves exactly one person their own share of one meeting. There is no
subject in the path — ``/me`` plus the authenticated user *is* the
authorisation, and there is no variant that names someone else. Nothing is
persisted. See docs/architecture/privacy.md section 3.

The ratio is withheld entirely when fewer than three participants consented:
with two, ``1 - ratio`` fixes the other person's share exactly, and an
above/below band mirrors just the same.
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


def _three_consenting(session: Session, meeting_id: str) -> dict[str, str]:
    """alice/bob/carol, all consenting, with user accounts. Speech added per test."""
    ids = {}
    for name in ("alice", "bob", "carol"):
        uid = _user(session, name)
        ids[name] = uid
        ids[f"p_{name}"] = _participant(session, meeting_id, user_id=uid, label=name.title())
    return ids


# --- endpoint -------------------------------------------------------------


def test_returns_the_requesters_own_ratio(app_for, db_session: Session, meeting: str) -> None:
    who = _three_consenting(db_session, meeting)
    _utter(db_session, meeting, who["p_alice"], 0.0, 30.0)
    _utter(db_session, meeting, who["p_bob"], 30.0, 40.0)
    _utter(db_session, meeting, who["p_carol"], 40.0, 50.0)
    db_session.flush()

    body = app_for(who["alice"]).get(f"/api/intelligence/me/speaking-ratio/{meeting}").json()

    assert body["ratio"] == pytest.approx(0.6)
    assert body["participant_count"] == 3
    assert body["reason"] is None
    assert body["stored"] is False


def test_a_consenting_silent_participant_gets_zero_not_a_404(
    app_for, db_session: Session, meeting: str
) -> None:
    who = _three_consenting(db_session, meeting)
    dave = _user(db_session, "dave")
    p_dave = _participant(db_session, meeting, user_id=dave, label="Dave")
    _utter(db_session, meeting, who["p_alice"], 0.0, 30.0)
    _utter(db_session, meeting, who["p_bob"], 30.0, 40.0)
    _utter(db_session, meeting, p_dave, 40.0, 60.0)
    db_session.flush()  # three speakers; carol consented but never spoke

    body = app_for(who["carol"]).get(f"/api/intelligence/me/speaking-ratio/{meeting}").json()

    assert body["ratio"] == 0.0
    assert body["reason"] is None


def test_ratio_is_withheld_when_fewer_than_three_consenting_participants_spoke(
    app_for, db_session: Session, meeting: str
) -> None:
    # three consenting participants, but carol only listened — the speech is
    # split two ways, so alice's ratio would fix bob's exactly.
    who = _three_consenting(db_session, meeting)
    _utter(db_session, meeting, who["p_alice"], 0.0, 30.0)
    _utter(db_session, meeting, who["p_bob"], 30.0, 40.0)
    db_session.flush()

    response = app_for(who["alice"]).get(f"/api/intelligence/me/speaking-ratio/{meeting}")

    assert response.status_code == 200  # not 404 — that means "not in this meeting"
    body = response.json()
    assert body["ratio"] is None
    assert body["reason"] == "small_meeting"
    assert body["participant_count"] == 3


def test_a_two_person_meeting_where_one_speaker_is_split_is_still_withheld(
    app_for, db_session: Session, meeting: str
) -> None:
    """Diarization can put one real speaker under two labels — two participant
    rows sharing a user_id once identified. That must still read as a two-person
    meeting, not three, or bob's response fixes alice's real ratio exactly via
    1 - ratio (docs/architecture/privacy.md section 3)."""
    alice = _user(db_session, "alice")
    bob = _user(db_session, "bob")
    p_alice_1 = _participant(db_session, meeting, user_id=alice, label="Speaker 0")
    p_alice_2 = _participant(db_session, meeting, user_id=alice, label="Speaker 2")
    p_bob = _participant(db_session, meeting, user_id=bob, label="Speaker 1")
    _utter(db_session, meeting, p_alice_1, 0.0, 20.0)
    _utter(db_session, meeting, p_alice_2, 20.0, 40.0)
    _utter(db_session, meeting, p_bob, 40.0, 60.0)
    db_session.flush()

    body = app_for(bob).get(f"/api/intelligence/me/speaking-ratio/{meeting}").json()

    assert body["ratio"] is None
    assert body["reason"] == "small_meeting"


def test_a_participant_split_across_two_rows_gets_their_full_ratio_not_half(
    app_for, db_session: Session, meeting: str
) -> None:
    """Alice's own number must be her combined share, not whichever of her two
    participant rows the lookup happens to pick."""
    alice = _user(db_session, "alice")
    bob = _user(db_session, "bob")
    carol = _user(db_session, "carol")
    p_alice_1 = _participant(db_session, meeting, user_id=alice, label="Speaker 0")
    p_alice_2 = _participant(db_session, meeting, user_id=alice, label="Speaker 2")
    p_bob = _participant(db_session, meeting, user_id=bob, label="Speaker 1")
    p_carol = _participant(db_session, meeting, user_id=carol, label="Speaker 3")
    _utter(db_session, meeting, p_alice_1, 0.0, 15.0)
    _utter(db_session, meeting, p_alice_2, 15.0, 30.0)
    _utter(db_session, meeting, p_bob, 30.0, 60.0)
    _utter(db_session, meeting, p_carol, 60.0, 90.0)
    db_session.flush()

    body = app_for(alice).get(f"/api/intelligence/me/speaking-ratio/{meeting}").json()

    assert body["ratio"] == pytest.approx(30.0 / 90.0)
    assert body["participant_count"] == 3


def test_withheld_when_the_other_speaker_is_unidentified_and_split(
    app_for, db_session: Session, meeting: str
) -> None:
    """Real two-person meeting: bob is identified, the other speaker (X) is
    consented but not yet identified (S16 hasn't run) and diarization split X
    across two labels. speaking_shares cannot merge X's two rows (no user_id to
    key on), so the gate must undercount unidentified labels itself, or bob
    reads X's exact ratio off 1 - his own."""
    bob = _user(db_session, "bob")
    p_x1 = _participant(db_session, meeting, user_id=None, label="Speaker 0")
    p_x2 = _participant(db_session, meeting, user_id=None, label="Speaker 2")
    p_bob = _participant(db_session, meeting, user_id=bob, label="Speaker 1")
    _utter(db_session, meeting, p_x1, 0.0, 20.0)
    _utter(db_session, meeting, p_x2, 20.0, 40.0)
    _utter(db_session, meeting, p_bob, 40.0, 60.0)
    db_session.flush()

    body = app_for(bob).get(f"/api/intelligence/me/speaking-ratio/{meeting}").json()

    assert body["ratio"] is None
    assert body["reason"] == "small_meeting"


def test_not_withheld_when_two_identified_and_one_unidentified_speaker_spoke(
    app_for, db_session: Session, meeting: str
) -> None:
    """A genuine three-person meeting (two identified, one not yet) must still
    release the ratio — undercounting unidentified labels should not punish a
    meeting that really does have enough people."""
    alice = _user(db_session, "alice")
    bob = _user(db_session, "bob")
    p_alice = _participant(db_session, meeting, user_id=alice, label="Speaker 0")
    p_bob = _participant(db_session, meeting, user_id=bob, label="Speaker 1")
    p_x = _participant(db_session, meeting, user_id=None, label="Speaker 2")
    _utter(db_session, meeting, p_alice, 0.0, 20.0)
    _utter(db_session, meeting, p_bob, 20.0, 40.0)
    _utter(db_session, meeting, p_x, 40.0, 60.0)
    db_session.flush()

    body = app_for(alice).get(f"/api/intelligence/me/speaking-ratio/{meeting}").json()

    assert body["ratio"] == pytest.approx(20.0 / 60.0)
    assert body["reason"] is None


def test_a_non_consenting_participant_is_told_they_are_not_measured(
    app_for, db_session: Session, meeting: str
) -> None:
    who = _three_consenting(db_session, meeting)
    dave = _user(db_session, "dave")
    p_dave = _participant(db_session, meeting, user_id=dave, label="Dave", consented=False)
    _utter(db_session, meeting, who["p_alice"], 0.0, 30.0)
    _utter(db_session, meeting, who["p_bob"], 30.0, 45.0)
    _utter(db_session, meeting, who["p_carol"], 45.0, 60.0)
    _utter(db_session, meeting, p_dave, 60.0, 120.0)
    db_session.flush()  # three consenting speakers, so the number is not withheld

    body = app_for(dave).get(f"/api/intelligence/me/speaking-ratio/{meeting}").json()

    assert body["ratio"] is None
    assert body["reason"] == "not_measured"


def test_not_measured_when_any_of_the_requesters_split_rows_did_not_consent(
    app_for, db_session: Session, meeting: str
) -> None:
    """A person split across two participant rows must get a deterministic
    answer regardless of which row a lookup happens to pick — not one that
    depends on row order. Withholding is the safe default when the rows
    disagree on consent."""
    who = _three_consenting(db_session, meeting)
    dave = _user(db_session, "dave")
    p_dave_1 = _participant(db_session, meeting, user_id=dave, label="Speaker 3", consented=True)
    _participant(db_session, meeting, user_id=dave, label="Speaker 4", consented=False)
    _utter(db_session, meeting, who["p_alice"], 0.0, 20.0)
    _utter(db_session, meeting, who["p_bob"], 20.0, 40.0)
    _utter(db_session, meeting, p_dave_1, 40.0, 60.0)
    db_session.flush()

    body = app_for(dave).get(f"/api/intelligence/me/speaking-ratio/{meeting}").json()

    assert body["reason"] == "not_measured"
    assert body["ratio"] is None


def test_404_when_the_requester_was_not_in_the_meeting(
    app_for, db_session: Session, meeting: str
) -> None:
    outsider = _user(db_session, "outsider")
    who = _three_consenting(db_session, meeting)
    _utter(db_session, meeting, who["p_alice"], 0.0, 30.0)
    db_session.flush()

    response = app_for(outsider).get(f"/api/intelligence/me/speaking-ratio/{meeting}")

    assert response.status_code == 404


def test_the_route_requires_authentication(app_for, meeting: str) -> None:
    response = app_for(None).get(f"/api/intelligence/me/speaking-ratio/{meeting}")

    assert response.status_code in (401, 403)


def test_no_intelligence_route_is_keyed_on_a_person() -> None:
    """The privacy guarantee is structural: no route takes a subject id, so
    there is no shape of a request that asks for someone else's data. This fails
    the moment a ``{user_id}`` / ``{subject_id}`` path parameter is added.
    """
    from autune_intelligence.router import router

    person_params = ("user_id", "subject_id", "participant_id", "speaker_id")
    offenders = [
        route.path
        for route in router.routes
        if any(f"{{{name}}}" in getattr(route, "path", "") for name in person_params)
    ]
    assert offenders == [], offenders


# --- service edge cases -------------------------------------------------


def test_compute_speaking_shares_excludes_unattributed_speech_from_the_denominator(
    db_session: Session, meeting: str
) -> None:
    alice = _user(db_session, "alice")
    p_alice = _participant(db_session, meeting, user_id=alice, label="Alice")
    _utter(db_session, meeting, p_alice, 0.0, 30.0)
    _utter(db_session, meeting, None, 30.0, 60.0)
    db_session.flush()

    shares = service.compute_speaking_shares(db_session, meeting)

    assert len(shares) == 1
    assert shares[0].ratio == pytest.approx(1.0)


def test_compute_speaking_shares_ignores_a_participant_who_did_not_consent(
    db_session: Session, meeting: str
) -> None:
    alice = _user(db_session, "alice")
    carol = _user(db_session, "carol")
    p_alice = _participant(db_session, meeting, user_id=alice, label="Alice")
    p_carol = _participant(db_session, meeting, user_id=carol, label="Carol", consented=False)
    _utter(db_session, meeting, p_alice, 0.0, 30.0)
    _utter(db_session, meeting, p_carol, 30.0, 90.0)
    db_session.flush()

    shares = service.compute_speaking_shares(db_session, meeting)

    assert [s.participant_id for s in shares] == [p_alice]
    assert shares[0].ratio == pytest.approx(1.0)
