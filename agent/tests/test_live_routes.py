"""``/api/agent/live``: members only, masked text only, caps answered, tasks queued."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_agent import router as routes
from autune_agent.live import routes as live_routes
from autune_agent.live.service import MAX_AUTO, open_document
from autune_agent.models import AgentLiveResearch
from autune_audio.models import AudConsentAttestation
from autune_core import AutuneError, TeamMember, User, current_user, get_session


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...]]]:
    calls: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.setattr(live_routes, "enqueue_detect", lambda *a: calls.append(("detect", a)))
    monkeypatch.setattr(live_routes, "enqueue_research", lambda *a: calls.append(("research", a)))
    return calls


@pytest.fixture(autouse=True)
def attested(session: Session, team: dict[str, str]) -> None:
    """The meeting's recording was started with the consent box ticked."""
    session.add(AudConsentAttestation(meeting_id=team["meeting"], attested_by=team["member"]))
    session.commit()


def _client(session: Session, user_id: str) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/agent")

    @app.exception_handler(AutuneError)
    async def _error(_: object, exc: AutuneError) -> object:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, user_id)
    return TestClient(app)


ROWS = {"rows": [{"start": 1.0, "text": "배포일이 언제였지?"}]}


def test_a_member_queues_a_detect(session: Session, team: dict[str, str], queued: list) -> None:
    reply = _client(session, team["member"]).post(
        f"/api/agent/live/{team['meeting']}/detect", json=ROWS
    )

    assert reply.status_code == 202
    assert queued == [("detect", (team["team"], team["meeting"], team["member"], ROWS["rows"]))]


def test_an_outsider_gets_404_and_nothing_is_queued(
    session: Session, team: dict[str, str], queued: list
) -> None:
    reply = _client(session, team["outsider"]).post(
        f"/api/agent/live/{team['meeting']}/detect", json=ROWS
    )

    assert reply.status_code == 404
    assert queued == []


def test_someone_who_left_mid_meeting_gets_404(
    session: Session, team: dict[str, str], queued: list
) -> None:
    session.query(TeamMember).filter_by(user_id=team["member"]).delete()
    session.commit()

    reply = _client(session, team["member"]).post(
        f"/api/agent/live/{team['meeting']}/detect", json=ROWS
    )

    assert reply.status_code == 404
    assert queued == []


def test_an_unmasked_row_is_refused_and_nothing_is_queued(
    session: Session, team: dict[str, str], queued: list
) -> None:
    rows = {"rows": [{"start": 1.0, "text": "제 번호는 010-1234-5678"}]}

    reply = _client(session, team["member"]).post(
        f"/api/agent/live/{team['meeting']}/detect", json=rows
    )

    assert reply.status_code == 500
    assert reply.json()["error"]["code"] == "privacy_violation"
    assert "010-1234-5678" not in reply.text
    assert queued == []


# Live research is covered by the consent attested when the recording starts;
# a meeting recorded with the box unticked is stored, not analysed, and its
# rows go nowhere (#1162 review).
def _unattest(session: Session, meeting_id: str) -> None:
    session.query(AudConsentAttestation).filter_by(meeting_id=meeting_id).delete()
    session.commit()


def test_detect_without_consent_is_refused_and_nothing_is_queued(
    session: Session, team: dict[str, str], queued: list
) -> None:
    _unattest(session, team["meeting"])

    reply = _client(session, team["member"]).post(
        f"/api/agent/live/{team['meeting']}/detect", json=ROWS
    )

    assert reply.status_code == 409
    assert reply.json() == {"code": "live_research_needs_consent"}
    assert queued == []


def test_research_without_consent_is_refused_and_no_document_is_opened(
    session: Session, team: dict[str, str], queued: list
) -> None:
    _unattest(session, team["meeting"])

    reply = _client(session, team["member"]).post(
        f"/api/agent/live/{team['meeting']}/research", json={"row": ROWS["rows"][0]}
    )

    assert reply.status_code == 409
    assert reply.json() == {"code": "live_research_needs_consent"}
    assert queued == []
    assert session.query(AgentLiveResearch).count() == 0


# A number read with a pause arrives as two rows, and neither row alone looks
# like one (audio-live-transcription.md, "A known limit of masking per row").
# The model gets the rows joined, so the joined text is what is checked.
SPLIT = [{"start": 12.0, "text": "연락은 010 1234"}, {"start": 13.0, "text": "5678 로 주세요"}]


def test_a_number_split_across_two_rows_is_refused_for_detect(
    session: Session, team: dict[str, str], queued: list
) -> None:
    reply = _client(session, team["member"]).post(
        f"/api/agent/live/{team['meeting']}/detect", json={"rows": SPLIT}
    )

    assert reply.status_code == 500
    assert reply.json()["error"]["code"] == "privacy_violation"
    assert "1234" not in reply.text
    assert queued == []


def test_a_number_split_between_context_and_row_is_refused_for_research(
    session: Session, team: dict[str, str], queued: list
) -> None:
    reply = _client(session, team["member"]).post(
        f"/api/agent/live/{team['meeting']}/research",
        json={"row": SPLIT[1], "context": [SPLIT[0]]},
    )

    assert reply.status_code == 500
    assert reply.json()["error"]["code"] == "privacy_violation"
    assert queued == []
    assert session.query(AgentLiveResearch).count() == 0


def test_too_many_rows_are_refused(session: Session, team: dict[str, str], queued: list) -> None:
    rows = {"rows": [{"start": float(i), "text": "말"} for i in range(13)]}

    reply = _client(session, team["member"]).post(
        f"/api/agent/live/{team['meeting']}/detect", json=rows
    )

    assert reply.status_code == 422
    assert queued == []


def test_detect_answers_429_once_the_meeting_has_five(
    session: Session, team: dict[str, str], queued: list
) -> None:
    for i in range(MAX_AUTO):
        open_document(
            session,
            team_id=team["team"],
            meeting_id=team["meeting"],
            user_id=None,
            origin="auto",
            question=f"q{i}",
        )

    reply = _client(session, team["member"]).post(
        f"/api/agent/live/{team['meeting']}/detect", json=ROWS
    )

    assert reply.status_code == 429
    assert queued == []


def test_research_opens_a_running_document_and_queues_it(
    session: Session, team: dict[str, str], queued: list
) -> None:
    body = {
        "row": {"start": 5.0, "text": "그 API 요금 얼마지?"},
        "context": [{"start": 3.0, "text": "외부 API 쓰자"}],
    }

    reply = _client(session, team["member"]).post(
        f"/api/agent/live/{team['meeting']}/research", json=body
    )

    assert reply.status_code == 202
    doc = session.get(AgentLiveResearch, reply.json()["id"])
    assert doc is not None and doc.status == "running" and doc.origin == "manual"
    assert doc.question == "그 API 요금 얼마지?"
    assert queued == [("research", (doc.id, body["context"], True))]


def test_documents_are_listed_newest_first_for_members_only(
    session: Session, team: dict[str, str]
) -> None:
    first = open_document(
        session,
        team_id=team["team"],
        meeting_id=team["meeting"],
        user_id=None,
        origin="auto",
        question="첫째",
    )
    second = open_document(
        session,
        team_id=team["team"],
        meeting_id=team["meeting"],
        user_id=None,
        origin="manual",
        question="둘째",
    )
    assert first is not None and second is not None
    first.created_at = datetime(2026, 10, 9, 1)
    second.created_at = datetime(2026, 10, 9, 2)
    session.commit()

    member = _client(session, team["member"]).get(f"/api/agent/live/{team['meeting']}/documents")
    outsider = _client(session, team["outsider"]).get(
        f"/api/agent/live/{team['meeting']}/documents"
    )

    assert member.status_code == 200
    assert [d["question"] for d in member.json()] == ["둘째", "첫째"]
    assert set(member.json()[0]) == {
        "id",
        "origin",
        "status",
        "question",
        "body",
        "web_sources",
        "meeting_sources",
        "created_at",
    }
    assert outsider.status_code == 404


# A queued window is meeting text in the broker: it expires instead of waiting
# for a worker that is down, and a stale one is useless anyway (#1162 review).
def test_queued_live_work_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    from autune_agent import tasks

    sent: list[dict[str, Any]] = []
    for name in ("live_detect", "live_research"):
        monkeypatch.setattr(getattr(tasks, name), "apply_async", lambda *a, **kw: sent.append(kw))

    live_routes.enqueue_detect("tm", "mtg", "usr", [{"start": 1.0, "text": "말"}])
    live_routes.enqueue_research("alr", [], True)

    assert [kw["expires"] for kw in sent] == [live_routes.QUEUE_EXPIRES_S] * 2
    assert live_routes.QUEUE_EXPIRES_S <= 120
