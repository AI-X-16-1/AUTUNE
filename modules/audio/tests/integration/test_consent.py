"""Consent, attested for a whole meeting by the person who uploads it.

Nothing in the repository sets ``participants.consented`` to True (#190), so
every real meeting comes out of B and C empty. Screen S10's per-attendee
consent table cannot exist before identification (#6) gives a voice a person;
until then the one honest statement available is a team member's: "everyone
in this recording consented". This is where that statement is recorded, and
what it does to the participant rows.

Two things module B asked for on #190 are pinned here: every label in an
attested meeting gets the same value, including labels a rerun invents
(``test_a_rerun_gives_a_new_label_the_same_consent``); and the True has a
provenance -- the attestation row itself, who and when.

What is *not* here: per-person consent, revocation, and re-publishing
``TranscriptReady`` when consent arrives after analysis. All three are S10/S11
and #190's follow-ups.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio import service
from autune_audio.models import AudConsentAttestation
from autune_audio.persistence import persist_transcript
from autune_audio.router import router
from autune_audio.speakers import Utterance as SpokenUtterance
from autune_core import AutuneError, Participant, TeamMember, User, get_session
from autune_core.auth import current_user
from autune_core.errors import NotFoundError, PermissionDeniedError


def spoken(speaker: str, start: float, text: str) -> SpokenUtterance:
    return SpokenUtterance(
        speaker=speaker, start=start, end=start + 2.0, text=text, words=(), confidence=0.9
    )


TWO_VOICES = (spoken("SPEAKER_00", 0.0, "시작하죠"), spoken("SPEAKER_01", 2.5, "네"))
THREE_VOICES = (*TWO_VOICES, spoken("SPEAKER_02", 5.0, "저도요"))


@pytest.fixture
def member(db_session: Session, team: str) -> User:
    user = User(email="member@example.com", display_name="팀원")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


@pytest.fixture
def outsider(db_session: Session) -> User:
    user = User(email="outsider@example.com", display_name="남")
    db_session.add(user)
    db_session.flush()
    return user


def consent_of(session: Session, meeting_id: str) -> dict[str, bool]:
    rows = session.scalars(sa.select(Participant).where(Participant.meeting_id == meeting_id))
    return {row.speaker_label: row.consented for row in rows}


# --- the attestation itself --------------------------------------------------


def test_a_member_attests_consent_for_a_meeting(
    db_session: Session, meeting: str, member: User
) -> None:
    service.attest_consent(db_session, meeting_id=meeting, attested_by=member)

    row = db_session.get(AudConsentAttestation, meeting)
    assert row is not None
    assert row.attested_by == member.id
    assert row.attested_at is not None


def test_attesting_twice_is_one_attestation(
    db_session: Session, meeting: str, member: User
) -> None:
    """A page reload is not a second statement."""
    service.attest_consent(db_session, meeting_id=meeting, attested_by=member)
    service.attest_consent(db_session, meeting_id=meeting, attested_by=member)

    count = db_session.scalar(
        sa.select(sa.func.count())
        .select_from(AudConsentAttestation)
        .where(AudConsentAttestation.meeting_id == meeting)
    )
    assert count == 1


def test_an_outsider_cannot_attest(db_session: Session, meeting: str, outsider: User) -> None:
    """Consent for a team's meeting is a member's statement to make."""
    with pytest.raises(PermissionDeniedError):
        service.attest_consent(db_session, meeting_id=meeting, attested_by=outsider)


def test_attesting_a_meeting_that_does_not_exist(db_session: Session, member: User) -> None:
    with pytest.raises(NotFoundError):
        service.attest_consent(db_session, meeting_id="mtg_nope", attested_by=member)


# --- what it does to the participants ----------------------------------------


def test_an_attested_meeting_gives_every_label_consent(
    db_session: Session, meeting: str, member: User
) -> None:
    service.attest_consent(db_session, meeting_id=meeting, attested_by=member)

    persist_transcript(
        db_session,
        meeting_id=meeting,
        utterances=TWO_VOICES,
        duration_seconds=5.0,
        audio_deleted=True,
    )

    assert consent_of(db_session, meeting) == {"SPEAKER_00": True, "SPEAKER_01": True}


def test_without_an_attestation_nothing_changes(db_session: Session, meeting: str) -> None:
    """The default is not loosened. No statement, no analysis -- as today."""
    persist_transcript(
        db_session,
        meeting_id=meeting,
        utterances=TWO_VOICES,
        duration_seconds=5.0,
        audio_deleted=True,
    )

    assert consent_of(db_session, meeting) == {"SPEAKER_00": False, "SPEAKER_01": False}


def test_a_rerun_gives_a_new_label_the_same_consent(
    db_session: Session, meeting: str, member: User
) -> None:
    """Module B's condition on #190, pinned.

    A rerun can cut the speakers differently and invent a label the first run
    never saw. Consent was given for the meeting, not for a set of labels, so
    the new voice is consented too -- otherwise its utterances silently vanish
    from B and C and the only symptom is "there are fewer decisions".
    """
    service.attest_consent(db_session, meeting_id=meeting, attested_by=member)
    persist_transcript(
        db_session,
        meeting_id=meeting,
        utterances=TWO_VOICES,
        duration_seconds=5.0,
        audio_deleted=True,
    )

    persist_transcript(
        db_session,
        meeting_id=meeting,
        utterances=THREE_VOICES,
        duration_seconds=8.0,
        audio_deleted=True,
    )

    assert consent_of(db_session, meeting) == {
        "SPEAKER_00": True,
        "SPEAKER_01": True,
        "SPEAKER_02": True,
    }


def test_an_attestation_after_the_transcript_reaches_the_rows_already_there(
    db_session: Session, meeting: str, member: User
) -> None:
    """Consent can arrive after analysis ran. The rows that exist are updated
    at once; whether B and C are told again is the republish question on
    #190, not this function's."""
    persist_transcript(
        db_session,
        meeting_id=meeting,
        utterances=TWO_VOICES,
        duration_seconds=5.0,
        audio_deleted=True,
    )
    assert consent_of(db_session, meeting) == {"SPEAKER_00": False, "SPEAKER_01": False}

    service.attest_consent(db_session, meeting_id=meeting, attested_by=member)

    assert consent_of(db_session, meeting) == {"SPEAKER_00": True, "SPEAKER_01": True}


def test_deleting_the_attestation_is_not_a_revocation(
    db_session: Session, meeting: str, member: User
) -> None:
    """There is no path from True back to False, and this pins that honestly.

    An earlier description of this PR said "un-attesting is deleting from one
    table". It is not (@PARKJAEKYUNG0525 on #283): the participant rows already
    set True stay True, and only labels a later rerun invents would come out
    False -- a meeting whose labels disagree about consent, which is the state
    module B's first condition on #190 forbids. So the row is not to be deleted
    by hand, and there is no route that deletes it. Revocation, when it exists,
    is S10/S11's, and it will have to reset the participant rows as well as
    remove this one.
    """
    service.attest_consent(db_session, meeting_id=meeting, attested_by=member)
    persist_transcript(
        db_session,
        meeting_id=meeting,
        utterances=TWO_VOICES,
        duration_seconds=5.0,
        audio_deleted=True,
    )

    db_session.delete(db_session.get(AudConsentAttestation, meeting))
    db_session.flush()

    assert consent_of(db_session, meeting) == {"SPEAKER_00": True, "SPEAKER_01": True}


def test_deleting_the_meeting_deletes_the_attestation(
    db_session: Session, meeting: str, member: User
) -> None:
    """privacy.md section 4: every derived table has a path to deletion."""
    service.attest_consent(db_session, meeting_id=meeting, attested_by=member)

    db_session.execute(sa.text("DELETE FROM meetings WHERE id = :id"), {"id": meeting})

    assert db_session.get(AudConsentAttestation, meeting) is None


# --- the route ------------------------------------------------------------------


@pytest.fixture
def app_for(db_session: Session):
    def _build(user: User | None) -> TestClient:
        app = FastAPI()

        @app.exception_handler(AutuneError)
        async def _render(_: Request, exc: AutuneError) -> JSONResponse:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        app.include_router(router, prefix="/api/audio")
        app.dependency_overrides[get_session] = lambda: db_session
        if user is not None:
            app.dependency_overrides[current_user] = lambda: user
        return TestClient(app)

    return _build


@pytest.fixture
def client(app_for, member: User) -> Iterator[TestClient]:
    yield app_for(member)


def test_the_route_records_the_attestation(
    client: TestClient, db_session: Session, meeting: str, member: User
) -> None:
    response = client.post(f"/api/audio/meetings/{meeting}/consent", json={"attested": True})

    assert response.status_code == 200
    assert response.json() == {"meeting_id": meeting, "attested": True}
    assert db_session.get(AudConsentAttestation, meeting).attested_by == member.id


def test_the_route_does_not_take_no_for_an_answer(client: TestClient, meeting: str) -> None:
    """``attested: false`` is not a revocation and not a no-op; it is a request
    this route has no meaning for. Revocation is S10/S11."""
    response = client.post(f"/api/audio/meetings/{meeting}/consent", json={"attested": False})

    assert response.status_code == 422


def test_the_route_without_a_token_is_refused(app_for, meeting: str) -> None:
    response = app_for(None).post(f"/api/audio/meetings/{meeting}/consent", json={"attested": True})

    assert response.status_code == 403
