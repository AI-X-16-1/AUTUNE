"""What the transcript screens ask for: the speakers of a meeting, and who
each one might be.

The vectors here are axis vectors, so every similarity is arithmetic: a
profile equal to the observation scores 1.0, an orthogonal one scores 0.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import quote

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio import service
from autune_audio.models import EMBEDDING_DIM, AudConsentAttestation, AudSpeakerEmbedding
from autune_audio.persistence import transcript_payload
from autune_audio.router import router
from autune_core import AutuneError, Participant, Team, TeamMember, User, Utterance, get_session
from autune_core.auth import current_user
from autune_core.deletion import run_user_hooks

MODEL_VERSION = "test/embedder"


def axis(index: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[index] = 1.0
    return v


def observation(
    meeting_id: str, label: str, vector: list[float], model_version: str = MODEL_VERSION
) -> AudSpeakerEmbedding:
    return AudSpeakerEmbedding(
        meeting_id=meeting_id, speaker_label=label, vector=vector, model_version=model_version
    )


def profile(
    user_id: str, vector: list[float], model_version: str = MODEL_VERSION
) -> AudSpeakerEmbedding:
    return AudSpeakerEmbedding(
        user_id=user_id, vector=vector, model_version=model_version, confirmed_by=user_id
    )


@pytest.fixture
def member(db_session: Session, team: str) -> User:
    user = User(email="member@example.com", display_name="팀원")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


@pytest.fixture
def candidate(db_session: Session, team: str) -> User:
    """Another member of the same team -- somebody the picker may offer."""
    user = User(email="candidate@example.com", display_name="후보")
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


@pytest.fixture
def other_team_member(db_session: Session) -> User:
    """Confirmed a voice, but never joined the meeting's own team."""
    other_team = Team(name="Other Team")
    db_session.add(other_team)
    db_session.flush()
    user = User(email="other@example.com", display_name="다른팀")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=other_team.id, user_id=user.id))
    db_session.flush()
    return user


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


# --- GET /meetings/{id}/speakers --------------------------------------------


def test_a_speaker_with_no_profile_has_no_candidate(
    client: TestClient, db_session: Session, meeting: str
) -> None:
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.add(observation(meeting, "화자 1", axis(0)))
    db_session.flush()

    [entry] = client.get(f"/api/audio/meetings/{meeting}/speakers").json()

    assert entry["speaker_label"] == "화자 1"
    assert entry["user_id"] is None
    assert entry["candidate"] is None


def test_a_matching_profile_is_offered_with_its_similarity(
    client: TestClient, db_session: Session, meeting: str, candidate: User
) -> None:
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.add(observation(meeting, "화자 1", axis(0)))
    db_session.add(profile(candidate.id, axis(0)))
    db_session.flush()

    [entry] = client.get(f"/api/audio/meetings/{meeting}/speakers").json()

    assert entry["candidate"]["user_id"] == candidate.id
    assert entry["candidate"]["name"] == candidate.display_name
    assert entry["candidate"]["similarity"] == pytest.approx(1.0)


def test_a_profile_in_another_team_is_never_a_candidate(
    client: TestClient, db_session: Session, meeting: str, other_team_member: User
) -> None:
    """The privacy test: same vector, but the profile's owner never joined
    this meeting's team -- offering them would say they attended it."""
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.add(observation(meeting, "화자 1", axis(0)))
    db_session.add(profile(other_team_member.id, axis(0)))
    db_session.flush()

    [entry] = client.get(f"/api/audio/meetings/{meeting}/speakers").json()

    assert entry["candidate"] is None


def test_a_profile_from_another_model_version_is_not_offered(
    client: TestClient, db_session: Session, meeting: str, candidate: User
) -> None:
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.add(observation(meeting, "화자 1", axis(0), model_version=MODEL_VERSION))
    db_session.add(profile(candidate.id, axis(0), model_version="other/embedder"))
    db_session.flush()

    [entry] = client.get(f"/api/audio/meetings/{meeting}/speakers").json()

    assert entry["candidate"] is None


def test_an_assigned_speaker_reports_its_user_and_no_candidate(
    client: TestClient, db_session: Session, meeting: str, candidate: User
) -> None:
    """There is nothing to propose once a person is already attached."""
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1", user_id=candidate.id))
    db_session.add(observation(meeting, "화자 1", axis(0)))
    db_session.add(profile(candidate.id, axis(0)))
    db_session.flush()

    [entry] = client.get(f"/api/audio/meetings/{meeting}/speakers").json()

    assert entry["user_id"] == candidate.id
    assert entry["candidate"] is None


def test_the_response_carries_no_counts_or_durations(
    client: TestClient, db_session: Session, meeting: str, candidate: User
) -> None:
    """The privacy test: a per-speaker count is a per-person speech volume,
    which nobody but the speaker may see -- and this route does not know who
    the speaker is, only who might be."""
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 2", user_id=candidate.id))
    db_session.add(observation(meeting, "화자 1", axis(0)))
    db_session.add(profile(candidate.id, axis(0)))
    db_session.flush()

    response = client.get(f"/api/audio/meetings/{meeting}/speakers")
    body = response.json()

    assert len(body) == 2
    for entry in body:
        assert set(entry) == {"speaker_label", "user_id", "candidate"}
    assert "count" not in response.text
    assert "seconds" not in response.text


def test_a_meeting_with_no_voice_recorded_yet_has_no_candidates(
    client: TestClient, db_session: Session, meeting: str
) -> None:
    """Spec §6: no consent attestation (so the worker never wrote an
    observation) means no candidates -- not an error. Nothing distinguishes
    this from any other "no observation row" case at read time, and it must
    not: a lookup that assumed `observations.get` always finds a row would
    break exactly here."""
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.flush()

    [entry] = client.get(f"/api/audio/meetings/{meeting}/speakers").json()

    assert entry["user_id"] is None
    assert entry["candidate"] is None


def test_ten_speakers_come_back_numbered_not_lexically_sorted(
    client: TestClient, db_session: Session, meeting: str
) -> None:
    """The case that catches a later "simplification" to a string sort: with
    ten speakers, `speaker_label` alone would put "화자 10" right after "화자
    1" and before "화자 2". Inserted out of order too, so the response cannot
    be passing by accident of insertion order (`Participant.id` is random and
    is only a tiebreak, never the ordering)."""
    labels = [f"화자 {n}" for n in range(1, 11)]
    insertion_order = [3, 1, 10, 7, 2, 9, 4, 8, 5, 6]
    for n in insertion_order:
        db_session.add(Participant(meeting_id=meeting, speaker_label=f"화자 {n}"))
    db_session.flush()

    body = client.get(f"/api/audio/meetings/{meeting}/speakers").json()

    assert [entry["speaker_label"] for entry in body] == labels


def test_an_outsider_cannot_read_a_meetings_speakers(
    app_for, db_session: Session, meeting: str, outsider: User
) -> None:
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.flush()

    response = app_for(outsider).get(f"/api/audio/meetings/{meeting}/speakers")

    assert response.status_code == 403


# --- GET /teams/{id}/members -------------------------------------------------


def test_members_lists_the_team_and_only_that_team(
    client: TestClient,
    app_for,
    db_session: Session,
    team: str,
    member: User,
    candidate: User,
    other_team_member: User,
    outsider: User,
) -> None:
    body = client.get(f"/api/audio/teams/{team}/members").json()

    assert len(body) == 2
    assert {(row["user_id"], row["name"]) for row in body} == {
        (member.id, member.display_name),
        (candidate.id, candidate.display_name),
    }
    assert other_team_member.id not in {row["user_id"] for row in body}

    response = app_for(outsider).get(f"/api/audio/teams/{team}/members")
    assert response.status_code == 403


# --- POST /meetings/{id}/speakers/{label} -----------------------------------


def _confirm(client: TestClient, meeting: str, label: str, user_id: str) -> httpx.Response:
    return client.post(
        f"/api/audio/meetings/{meeting}/speakers/{quote(label)}", json={"user_id": user_id}
    )


def test_confirming_fills_the_speaker_and_copies_the_profile(
    client: TestClient, db_session: Session, meeting: str, member: User, candidate: User
) -> None:
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.add(observation(meeting, "화자 1", axis(0)))
    db_session.flush()

    response = _confirm(client, meeting, "화자 1", candidate.id)

    assert response.status_code == 204
    participant = db_session.scalar(
        sa.select(Participant).where(
            Participant.meeting_id == meeting, Participant.speaker_label == "화자 1"
        )
    )
    assert participant is not None
    assert participant.user_id == candidate.id

    new_profile = db_session.scalar(
        sa.select(AudSpeakerEmbedding).where(
            AudSpeakerEmbedding.source_meeting_id == meeting,
            AudSpeakerEmbedding.source_speaker_label == "화자 1",
        )
    )
    assert new_profile is not None
    assert new_profile.user_id == candidate.id
    assert new_profile.confirmed_by == member.id
    assert new_profile.confirmed_at is not None


def test_the_transcript_then_carries_the_speaker_id(
    client: TestClient, db_session: Session, meeting: str, candidate: User
) -> None:
    participant = Participant(meeting_id=meeting, speaker_label="화자 1")
    db_session.add(participant)
    db_session.add(observation(meeting, "화자 1", axis(0)))
    db_session.flush()
    db_session.add(
        Utterance(
            meeting_id=meeting,
            participant_id=participant.id,
            speaker_label="화자 1",
            start_sec=0.0,
            end_sec=1.0,
            text="안녕하세요",
        )
    )
    db_session.flush()

    response = _confirm(client, meeting, "화자 1", candidate.id)
    assert response.status_code == 204

    payload = transcript_payload(db_session, meeting_id=meeting)
    [utterance] = payload.utterances
    assert utterance.speaker_id == candidate.id


def test_confirming_again_replaces_the_profile_from_that_source(
    client: TestClient, db_session: Session, meeting: str, member: User, candidate: User
) -> None:
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.add(observation(meeting, "화자 1", axis(0)))
    db_session.flush()

    first = _confirm(client, meeting, "화자 1", member.id)
    assert first.status_code == 204
    second = _confirm(client, meeting, "화자 1", candidate.id)
    assert second.status_code == 204

    rows = list(
        db_session.scalars(
            sa.select(AudSpeakerEmbedding).where(
                AudSpeakerEmbedding.source_meeting_id == meeting,
                AudSpeakerEmbedding.source_speaker_label == "화자 1",
            )
        )
    )
    assert len(rows) == 1
    assert rows[0].user_id == candidate.id

    member_profiles = list(
        db_session.scalars(
            sa.select(AudSpeakerEmbedding).where(AudSpeakerEmbedding.user_id == member.id)
        )
    )
    assert member_profiles == []


def test_confirming_again_replaces_the_profile_even_without_a_new_observation(
    client: TestClient, db_session: Session, meeting: str, member: User, candidate: User
) -> None:
    """The bug the "replaces" delete used to have: it only ran when a fresh
    observation existed to replace the old profile with. Here the meeting is
    reprocessed between the two confirmations -- the observation this
    source's profile came from is gone by the time the correction lands, as
    it legitimately can be (``tasks._store_speaker_embeddings`` deletes and
    re-derives a meeting's observations, and a short-spoken label may not
    come back). The wrong person's voice must not survive that gap."""
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.add(observation(meeting, "화자 1", axis(0)))
    db_session.flush()

    first = _confirm(client, meeting, "화자 1", member.id)
    assert first.status_code == 204

    # The reprocess: the observation this profile was sourced from is gone.
    db_session.execute(
        sa.delete(AudSpeakerEmbedding).where(
            AudSpeakerEmbedding.meeting_id == meeting,
            AudSpeakerEmbedding.speaker_label == "화자 1",
        )
    )
    db_session.flush()

    second = _confirm(client, meeting, "화자 1", candidate.id)
    assert second.status_code == 204

    member_profiles = list(
        db_session.scalars(
            sa.select(AudSpeakerEmbedding).where(AudSpeakerEmbedding.user_id == member.id)
        )
    )
    assert member_profiles == []

    from_this_source = list(
        db_session.scalars(
            sa.select(AudSpeakerEmbedding).where(
                AudSpeakerEmbedding.source_meeting_id == meeting,
                AudSpeakerEmbedding.source_speaker_label == "화자 1",
            )
        )
    )
    assert from_this_source == []


def test_a_failed_profile_copy_leaves_the_source_with_no_profile_not_a_stale_one(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    meeting: str,
    candidate: User,
) -> None:
    """The INSERT can fail on its own -- a wrong vector width is pgvector's
    dimension check on ``Vector(256)``, the same failure Task 4 exercises in
    ``test_tasks.py::test_a_bad_vector_does_not_fail_the_meeting``. The
    DELETE already ran and is not inside the INSERT's SAVEPOINT, so a failed
    copy leaves this source with *no* profile -- never with the stale one
    that was just declared wrong."""
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.add(observation(meeting, "화자 1", axis(0)))
    db_session.flush()

    real_add = db_session.add

    def _corrupt_the_copy(instance: object) -> None:
        # Only the profile-copy INSERT is targeted -- it is the one
        # ``AudSpeakerEmbedding`` this function ever constructs with a
        # ``source_meeting_id``. Everything else this test or the route adds
        # (the ``Participant``, the observation above) passes through.
        if isinstance(instance, AudSpeakerEmbedding) and instance.source_meeting_id is not None:
            instance.vector = instance.vector[:4]
        real_add(instance)

    monkeypatch.setattr(db_session, "add", _corrupt_the_copy)

    response = _confirm(client, meeting, "화자 1", candidate.id)

    assert response.status_code == 204
    participant = db_session.scalar(
        sa.select(Participant).where(
            Participant.meeting_id == meeting, Participant.speaker_label == "화자 1"
        )
    )
    assert participant is not None
    assert participant.user_id == candidate.id

    from_this_source = list(
        db_session.scalars(
            sa.select(AudSpeakerEmbedding).where(
                AudSpeakerEmbedding.source_meeting_id == meeting,
                AudSpeakerEmbedding.source_speaker_label == "화자 1",
            )
        )
    )
    assert from_this_source == []

    profiles = list(
        db_session.scalars(
            sa.select(AudSpeakerEmbedding).where(AudSpeakerEmbedding.user_id == candidate.id)
        )
    )
    assert profiles == []


def test_confirming_without_an_observation_still_assigns(
    client: TestClient, db_session: Session, meeting: str, candidate: User
) -> None:
    """No consent, no observation row -- assigning still works, no profile,
    no error (the normal case, not an edge)."""
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.flush()

    response = _confirm(client, meeting, "화자 1", candidate.id)

    assert response.status_code == 204
    participant = db_session.scalar(
        sa.select(Participant).where(
            Participant.meeting_id == meeting, Participant.speaker_label == "화자 1"
        )
    )
    assert participant is not None
    assert participant.user_id == candidate.id

    profiles = list(
        db_session.scalars(
            sa.select(AudSpeakerEmbedding).where(AudSpeakerEmbedding.user_id == candidate.id)
        )
    )
    assert profiles == []


def test_confirming_an_unknown_label_is_404(
    client: TestClient, db_session: Session, meeting: str, candidate: User
) -> None:
    response = _confirm(client, meeting, "화자 9", candidate.id)

    assert response.status_code == 404


def test_an_outsider_cannot_confirm(
    app_for, db_session: Session, meeting: str, outsider: User, candidate: User
) -> None:
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.flush()

    response = _confirm(app_for(outsider), meeting, "화자 1", candidate.id)

    assert response.status_code == 403
    # The one endpoint that mints biometric data: a 403 alone would still
    # pass if the checks ran after the writes. Nothing must have moved.
    participant = db_session.scalar(
        sa.select(Participant).where(
            Participant.meeting_id == meeting, Participant.speaker_label == "화자 1"
        )
    )
    assert participant is not None
    assert participant.user_id is None
    profiles = list(
        db_session.scalars(
            sa.select(AudSpeakerEmbedding).where(AudSpeakerEmbedding.user_id == candidate.id)
        )
    )
    assert profiles == []


def test_a_user_outside_the_team_cannot_be_assigned(
    client: TestClient, db_session: Session, meeting: str, other_team_member: User
) -> None:
    db_session.add(Participant(meeting_id=meeting, speaker_label="화자 1"))
    db_session.flush()

    response = _confirm(client, meeting, "화자 1", other_team_member.id)

    assert response.status_code == 403
    participant = db_session.scalar(
        sa.select(Participant).where(
            Participant.meeting_id == meeting, Participant.speaker_label == "화자 1"
        )
    )
    assert participant is not None
    assert participant.user_id is None
    profiles = list(
        db_session.scalars(
            sa.select(AudSpeakerEmbedding).where(
                AudSpeakerEmbedding.user_id == other_team_member.id
            )
        )
    )
    assert profiles == []


# --- DELETE /me/voice-profile ------------------------------------------------


def test_deleting_my_voice_profile_removes_only_mine(
    client: TestClient, db_session: Session, member: User, candidate: User
) -> None:
    db_session.add(profile(member.id, axis(0)))
    db_session.add(profile(candidate.id, axis(1)))
    db_session.flush()

    response = client.delete("/api/audio/me/voice-profile")

    assert response.status_code == 204
    remaining = list(db_session.scalars(sa.select(AudSpeakerEmbedding.user_id).distinct()))
    assert remaining == [candidate.id]


# --- forget_user_voice --------------------------------------------------------


def test_the_deletion_hook_removes_a_users_profiles(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, member: User, candidate: User
) -> None:
    """Driven through ``autune_core.deletion.run_user_hooks`` rather than
    calling ``service.forget_user_voice`` directly, so this also pins that
    the ``@on_user_deleted("audio")`` registration itself is live -- deleting
    the decorator would not fail a test that only calls the function by
    name."""
    db_session.add(profile(member.id, axis(0)))
    db_session.add(profile(candidate.id, axis(1)))
    db_session.flush()

    @contextmanager
    def fake_session_scope() -> Iterator[Session]:
        yield db_session
        db_session.flush()

    monkeypatch.setattr(service, "session_scope", fake_session_scope)

    run_user_hooks(member.id)

    remaining = list(db_session.scalars(sa.select(AudSpeakerEmbedding.user_id).distinct()))
    assert remaining == [candidate.id]


def test_the_deletion_hook_clears_confirmed_by_and_attested_by_elsewhere(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    meeting: str,
    member: User,
    candidate: User,
) -> None:
    """Leaving the product clears more than the person's own rows: their id
    must not linger as *who confirmed* somebody else's profile, or *who
    attested* a meeting's consent -- both are ids the product no longer has
    anyone behind."""
    db_session.add(
        AudSpeakerEmbedding(
            user_id=candidate.id,
            vector=axis(0),
            model_version=MODEL_VERSION,
            confirmed_by=member.id,
        )
    )
    db_session.add(AudConsentAttestation(meeting_id=meeting, attested_by=member.id))
    db_session.flush()

    @contextmanager
    def fake_session_scope() -> Iterator[Session]:
        yield db_session
        db_session.flush()

    monkeypatch.setattr(service, "session_scope", fake_session_scope)

    run_user_hooks(member.id)

    candidates_profile = db_session.scalar(
        sa.select(AudSpeakerEmbedding).where(AudSpeakerEmbedding.user_id == candidate.id)
    )
    assert candidates_profile is not None
    assert candidates_profile.confirmed_by is None

    attestation = db_session.get(AudConsentAttestation, meeting)
    assert attestation is not None
    assert attestation.attested_by is None
