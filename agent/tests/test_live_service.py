"""Live research service on SQLite: caps, dedupe, sources, and failure kept visible."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_agent.live.model import Detected, Quote, Row
from autune_agent.live.service import (
    MAX_AUTO,
    detect_and_research,
    open_document,
    research,
)
from autune_agent.main.gemini import WebAnswer
from autune_agent.main.registry import Tool
from autune_agent.models import AgentLiveResearch, AgentLiveResearchSource
from autune_core import Meeting
from autune_core.errors import PrivacyViolationError


class FakeModel:
    def __init__(
        self, *, detected: list[Detected] | None = None, body: str = "제목\n- 내용"
    ) -> None:
        self.detected = detected or []
        self.body = body
        self.written: list[dict[str, object]] = []
        self.web_asked: list[str] = []

    def detect(self, rows: Sequence[Row], known: Sequence[str]) -> list[Detected]:
        return [d for d in self.detected if d.question not in known]

    def terms(self, question: str) -> list[str]:
        return ["배포"]

    def web(self, question: str) -> WebAnswer:
        self.web_asked.append(question)
        return WebAnswer(text="웹 답", sources=[("p", "https://a.test")])

    def write(self, question, context, quotes, web) -> str:  # type: ignore[no-untyped-def]
        self.written.append({"question": question, "quotes": list(quotes), "web": web})
        return self.body


def _search_tool(past_meeting_id: str) -> dict[str, Tool]:
    def search_team_meetings(session, team_id, query, *, days=90, exclude_meeting_id=None):  # type: ignore[no-untyped-def]
        return {
            "ok": True,
            "summary": "1건",
            "items": [
                {
                    "title": "2026-09-10 배포 회의 · 03:10 김민경",
                    "body": "금요일에 배포하죠",
                    "id": "utt_past1",
                    "meeting_id": past_meeting_id,
                }
            ],
            "evidence": ["utt_past1"],
            "confidence": 0.8,
        }

    return {
        "audio.search_team_meetings": Tool(
            "audio.search_team_meetings", "search", search_team_meetings
        )
    }


@pytest.fixture
def past(session: Session, team: dict[str, str]) -> str:
    meeting = Meeting(team_id=team["team"], title="배포 회의")
    session.add(meeting)
    session.commit()
    return meeting.id


def _open(
    session: Session, team: dict[str, str], question: str, origin: str = "auto"
) -> AgentLiveResearch | None:
    return open_document(
        session,
        team_id=team["team"],
        meeting_id=team["meeting"],
        user_id=team["member"],
        origin=origin,
        question=question,
    )


def test_a_question_asked_twice_opens_one_document(session: Session, team: dict[str, str]) -> None:
    assert _open(session, team, "배포일이 언제였지?") is not None
    assert _open(session, team, "  배포일이   언제였지? ") is None


def test_the_automatic_cap_stops_the_sixth(session: Session, team: dict[str, str]) -> None:
    for i in range(MAX_AUTO):
        assert _open(session, team, f"질문 {i}") is not None
    assert _open(session, team, "여섯째") is None
    assert _open(session, team, "직접 요청", origin="manual") is not None


def test_research_quotes_past_meetings_without_the_speaker(
    session: Session, team: dict[str, str], past: str
) -> None:
    doc = _open(session, team, "배포일이 언제였지?")
    assert doc is not None
    model = FakeModel()

    research(session, doc.id, context=[], model=model, web=False, tools=_search_tool(past))

    session.refresh(doc)
    assert doc.status == "done"
    assert doc.body == "제목\n- 내용"
    assert doc.meeting_sources == [{"meeting_id": past, "title": "2026-09-10 배포 회의"}]
    quote = model.written[0]["quotes"][0]  # type: ignore[index]
    assert quote == Quote(meeting_id=past, title="2026-09-10 배포 회의", body="금요일에 배포하죠")
    sources = session.scalars(select(AgentLiveResearchSource.meeting_id)).all()
    assert sources == [past]
    assert model.web_asked == []


def test_research_asks_the_web_when_told(session: Session, team: dict[str, str], past: str) -> None:
    doc = _open(session, team, "API 요금?", origin="manual")
    assert doc is not None
    model = FakeModel()

    research(session, doc.id, context=[], model=model, web=True, tools=_search_tool(past))

    session.refresh(doc)
    assert doc.web_sources == [{"title": "p", "url": "https://a.test"}]
    assert model.web_asked == ["API 요금?"]


def test_a_writer_failure_leaves_a_failed_document(session: Session, team: dict[str, str]) -> None:
    doc = _open(session, team, "질문")
    assert doc is not None

    class Broken(FakeModel):
        def write(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("model down")

    research(session, doc.id, context=[], model=Broken(), web=False, tools={})

    session.refresh(doc)
    assert doc.status == "failed"
    assert doc.body is None


def test_no_model_key_leaves_a_failed_document(session: Session, team: dict[str, str]) -> None:
    from autune_core.errors import ConfigurationError

    doc = _open(session, team, "질문")
    assert doc is not None

    class Off(FakeModel):
        def terms(self, question: str) -> list[str]:
            raise ConfigurationError("off")

        def write(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise ConfigurationError("off")

    research(session, doc.id, context=[], model=Off(), web=False, tools={})

    session.refresh(doc)
    assert doc.status == "failed"


def test_an_unmasked_body_is_never_stored(session: Session, team: dict[str, str]) -> None:
    doc = _open(session, team, "질문")
    assert doc is not None

    with pytest.raises(PrivacyViolationError):
        research(
            session,
            doc.id,
            context=[],
            model=FakeModel(body="연락처 010-1234-5678"),
            web=False,
            tools={},
        )
    session.rollback()
    assert session.get(AgentLiveResearch, doc.id).body is None  # type: ignore[union-attr]


def test_detect_and_research_makes_one_document_per_new_question(
    session: Session, team: dict[str, str], past: str
) -> None:
    model = FakeModel(detected=[Detected("배포일?", False, ["배포"]), Detected("요금?", True, [])])

    made = detect_and_research(
        session,
        team_id=team["team"],
        meeting_id=team["meeting"],
        user_id=team["member"],
        rows=[Row(1.0, "배포일 언제였지")],
        model=model,
        tools=_search_tool(past),
    )

    assert len(made) == 2
    statuses = session.scalars(select(AgentLiveResearch.status)).all()
    assert statuses == ["done", "done"]
