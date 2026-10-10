"""S15's 요약 tab, v1 (#421): B's rows in three levels, and the team's memo.

SQLite in memory and the router on a bare app. Under test: which decisions and
items the summary lists, what it counts, and that the memo is replaced whole,
removed when blank, and readable by the meeting's team only.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import AutuneError, Base, Meeting, TeamMember, User, Utterance, get_session
from autune_extraction import service
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import (
    ExtActionItem,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionReview,
)
from autune_extraction.router import router

from .conftest import sign_in

PREFIX = "/api/extraction"
MEETING = "mtg_1"


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTUNE_EXTRACTION_CANDIDATE_CONFIDENCE", raising=False)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, TeamMember, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        s.add(Meeting(id="mtg_other", team_id="team_2", title="다른 팀 회의"))
        s.flush()
        yield s


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session)
    yield TestClient(app)


def decision(session: Session, decision_id: str, statement: str, verdict: str | None) -> None:
    session.add(
        ExtDecision(id=decision_id, meeting_id=MEETING, statement=statement, confidence=0.8)
    )
    session.flush()
    if verdict is not None:
        session.add(ExtDecisionReview(decision_id=decision_id, meeting_id=MEETING, status=verdict))
        session.flush()


def item(session: Session, item_id: str, status: str) -> None:
    session.add(
        ExtActionItem(
            id=item_id,
            meeting_id=MEETING,
            description=f"{item_id} 할 일",
            status=status,
            confidence=0.9,
            origin="model",
        )
    )
    session.flush()


def test_the_summary_names_the_meeting_and_its_day_with_no_row_to_take_them_from(
    session: Session,
) -> None:
    """The page of minutes is headed with these. A meeting with no action item
    used to have no title on it: the title came from the first item."""
    began = datetime(2026, 10, 8, 1, 0, tzinfo=UTC)
    session.get(Meeting, MEETING).started_at = began
    session.flush()

    summary = service.meeting_summary(session, MEETING)

    assert summary.action_items == []
    assert summary.meeting_title == "주간 회의"
    assert summary.meeting_started_at is not None
    assert summary.meeting_started_at.replace(tzinfo=UTC) == began


def test_the_summary_lists_what_the_meeting_settled_and_left(session: Session) -> None:
    decision(session, "dec_pending", "배포는 금요일로 하기로 했습니다", None)
    decision(session, "dec_yes", "검색은 인기순으로 하기로 했습니다", "confirmed")
    decision(session, "dec_no", "회의실은 예약제로 하기로 했습니다", "rejected")
    item(session, "act_draft", "needs_confirmation")
    item(session, "act_done", "done")
    for n, kind in enumerate(("open_question", "open_question", "concern")):
        session.add(
            ExtClassification(
                utterance_id=f"utt_{n}",
                meeting_id=MEETING,
                kind=kind,
                confidence=0.9,
                model_version="test",
                nli_verified=False,
            )
        )
    now = datetime.now(UTC)
    session.add_all(
        [
            ExtConfirmation(utterance_id="utt_a", meeting_id=MEETING, reason="weak_assent"),
            ExtConfirmation(
                utterance_id="utt_b", meeting_id=MEETING, reason="weak_assent", sent_at=now
            ),
            ExtConfirmation(
                utterance_id="utt_c",
                meeting_id=MEETING,
                reason="weak_assent",
                sent_at=now,
                resolved_kind="commitment",
                responded_at=now,
            ),
        ]
    )
    session.flush()

    summary = service.meeting_summary(session, MEETING, now=now)

    assert [(d.id, d.status) for d in summary.decisions] == [
        ("dec_yes", "confirmed"),
        ("dec_pending", "pending"),
    ]
    assert sorted(i.id for i in summary.action_items) == ["act_done", "act_draft"]
    assert summary.open_questions == 2
    assert summary.ambiguous_waiting == 2, "never asked, and asked but unanswered"
    assert summary.note is None


def test_the_memo_is_replaced_whole_and_blank_removes_it(
    client: TestClient, session: Session
) -> None:
    first = client.put(
        f"{PREFIX}/summary/{MEETING}/note", json={"body": "  다음 회의 전 예산 확인  "}
    )
    assert first.status_code == 200
    assert first.json()["note"] == "다음 회의 전 예산 확인"
    assert first.json()["note_updated_at"] is not None

    client.put(f"{PREFIX}/summary/{MEETING}/note", json={"body": "예산 확인 끝"})
    assert client.get(f"{PREFIX}/summary/{MEETING}").json()["note"] == "예산 확인 끝"

    cleared = client.put(f"{PREFIX}/summary/{MEETING}/note", json={"body": "   "})
    assert cleared.json()["note"] is None


def test_a_memo_longer_than_the_limit_is_refused(client: TestClient) -> None:
    response = client.put(f"{PREFIX}/summary/{MEETING}/note", json={"body": "가" * 2001})

    assert response.status_code == 422


def test_another_teams_meeting_is_not_found(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/summary/mtg_other").status_code == 404
    response = client.put(f"{PREFIX}/summary/mtg_other/note", json={"body": "남의 회의"})
    assert response.status_code == 404
