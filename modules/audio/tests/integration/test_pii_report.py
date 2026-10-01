"""S30 against a real database: the span is masked where it is stored, and B, C, D are told.

Read `modules/audio/tests/integration/conftest.py` for `db_session`.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio import router as router_module
from autune_audio.router import router
from autune_contracts.events import TRANSCRIPT_READY
from autune_core import AutuneError, Meeting, Participant, TeamMember, User, Utterance, get_session
from autune_core.auth import current_user

TEXT = "담당자는 박OO 과장이고 사번 A-20391 로 등록돼 있어요"
SPAN = "A-20391"


@pytest.fixture
def member(db_session: Session, team: str) -> User:
    user = User(email="reporter@example.com", display_name="신고자")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


def say(session: Session, meeting: str, text: str) -> str:
    participant = Participant(meeting_id=meeting, speaker_label="화자 1")
    session.add(participant)
    session.flush()
    row = Utterance(
        meeting_id=meeting,
        participant_id=participant.id,
        speaker_label="화자 1",
        start_sec=0,
        end_sec=1,
        text=text,
    )
    session.add(row)
    session.flush()
    return row.id


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    sent: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(router_module, "publish", lambda name, body: sent.append((name, body)))
    return sent


@pytest.fixture
def client_for(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    # The route commits before it publishes; inside the test's outer
    # transaction a commit would end it, so it is a flush here.
    monkeypatch.setattr(db_session, "commit", db_session.flush)

    def build(user: User) -> TestClient:
        app = FastAPI()

        @app.exception_handler(AutuneError)
        async def render(_: Request, exc: AutuneError) -> JSONResponse:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        app.include_router(router, prefix="/api/audio")
        app.dependency_overrides[get_session] = lambda: db_session
        app.dependency_overrides[current_user] = lambda: user
        return TestClient(app)

    return build


def report(client: TestClient, meeting: str, utterance: str, **body: Any):
    start = TEXT.index(SPAN)
    payload = {"start": start, "end": start + len(SPAN), "category": "internal_id", **body}
    return client.post(
        f"/api/audio/meetings/{meeting}/utterances/{utterance}/pii-report", json=payload
    )


def test_the_span_is_masked_in_the_stored_row_and_announced(
    db_session: Session, client_for, member: User, meeting: str, published: list
) -> None:
    db_session.get(Meeting, meeting).status = "complete"  # type: ignore[union-attr]
    utterance = say(db_session, meeting, TEXT)

    response = report(client_for(member), meeting, utterance)

    assert response.json() == {"utterances": 1, "occurrences": 1, "republished": True}
    stored = db_session.get(Utterance, utterance)
    assert stored is not None and SPAN not in stored.text and "*-*****" in stored.text
    [(name, body)] = published
    assert name == TRANSCRIPT_READY
    assert SPAN not in str(body)


def test_exact_repeats_elsewhere_in_the_meeting_go_too(
    db_session: Session, client_for, member: User, meeting: str, published: list
) -> None:
    utterance = say(db_session, meeting, TEXT)
    again = say(db_session, meeting, f"다시 말하면 {SPAN} 입니다")
    untouched = say(db_session, meeting, "관계없는 말")

    response = report(client_for(member), meeting, utterance)

    assert response.json()["utterances"] == 2
    assert SPAN not in db_session.get(Utterance, again).text  # type: ignore[union-attr]
    assert db_session.get(Utterance, untouched).text == "관계없는 말"  # type: ignore[union-attr]


def test_repeats_stay_when_not_asked(
    db_session: Session, client_for, member: User, meeting: str, published: list
) -> None:
    utterance = say(db_session, meeting, TEXT)
    again = say(db_session, meeting, f"다시 {SPAN}")

    report(client_for(member), meeting, utterance, include_similar=False)

    assert SPAN in db_session.get(Utterance, again).text  # type: ignore[union-attr]


def test_a_meeting_never_announced_is_not_announced_now(
    db_session: Session, client_for, member: User, meeting: str, published: list
) -> None:
    db_session.get(Meeting, meeting).status = "analyzing"  # type: ignore[union-attr]
    utterance = say(db_session, meeting, TEXT)

    assert report(client_for(member), meeting, utterance).json()["republished"] is False
    assert published == []


def test_offsets_outside_the_text_are_refused_without_quoting_it(
    db_session: Session, client_for, member: User, meeting: str, published: list
) -> None:
    utterance = say(db_session, meeting, TEXT)

    response = client_for(member).post(
        f"/api/audio/meetings/{meeting}/utterances/{utterance}/pii-report",
        json={"start": 0, "end": 999, "category": "other"},
    )

    assert response.status_code == 422
    assert "담당자" not in response.text


def test_an_outsider_cannot_report(
    db_session: Session, client_for, meeting: str, published: list
) -> None:
    outsider = User(email="out@example.com", display_name="남")
    db_session.add(outsider)
    db_session.flush()
    utterance = say(db_session, meeting, TEXT)

    assert report(client_for(outsider), meeting, utterance).status_code == 403
    assert db_session.get(Utterance, utterance).text == TEXT  # type: ignore[union-attr]
