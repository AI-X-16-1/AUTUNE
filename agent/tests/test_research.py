"""Research: read, terms, search, write, save and propose (spec section 3)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy.orm import Session

from autune_agent.main import CallBudget, RunScope, Toolbox, collect_subagents
from autune_agent.main.registry import Tool
from autune_agent.subagents.research import SUBAGENT, make_subagent
from autune_agent.subagents.research.graph import (
    OVERVIEW,
    QUESTIONS,
    RECENT,
    SAVE,
    SEARCH,
    SHARE,
)
from autune_agent.subagents.research.writer import Match, WriterError
from autune_contracts import INTELLIGENCE_COMPLETED
from autune_core.errors import PrivacyViolationError


class FakeWriter:
    def __init__(self, terms: list[str] | None = None, fail: bool = False) -> None:
        self._terms = ["배포"] if terms is None else terms
        self.fail = fail
        self.written: list[dict[str, Any]] = []

    def terms(self, questions: Sequence[str]) -> list[str]:
        return self._terms

    def write(
        self, *, meeting_title: str, questions: Sequence[str], matches: Sequence[Match]
    ) -> str:
        if self.fail:
            raise WriterError("empty document")
        self.written.append({"questions": list(questions), "matches": list(matches)})
        return "## 제기된 질문\n- 배포"


def _tool(name: str, fn: Any) -> Tool:
    return Tool(name=name, description="Use this in tests.", fn=fn)


def tools_for(
    *,
    questions: list[dict[str, Any]] | None = None,
    matches: list[dict[str, Any]] | None = None,
    recent: list[dict[str, Any]] | None = None,
    calls: list[str] | None = None,
) -> dict[str, Tool]:
    log = [] if calls is None else calls
    qs = (
        [{"title": "질문", "body": "배포는 언제죠?", "id": "utt_q1"}]
        if questions is None
        else questions
    )

    def overview(session: Any, meeting_id: str) -> dict[str, Any]:
        log.append(OVERVIEW)
        return {"ok": True, "summary": "「스프린트」 · 분석 완료.", "evidence": [meeting_id]}

    def unresolved(session: Any, meeting_id: str) -> dict[str, Any]:
        log.append(QUESTIONS)
        return {"ok": True, "summary": f"질문 {len(qs)}건", "items": qs, "evidence": []}

    def recent_meetings(session: Any, team_id: str) -> dict[str, Any]:
        log.append(RECENT)
        items = recent if recent is not None else []
        return {"ok": True, "summary": "회의", "items": items, "evidence": []}

    def search(
        session: Any, team_id: str, query: str, exclude_meeting_id: str | None = None
    ) -> dict[str, Any]:
        log.append(SEARCH)
        ms = (
            matches
            if matches is not None
            else [
                {
                    "title": "2026-09-23 리뷰 · 00:03 김팀장",
                    "body": "금요일 배포",
                    "id": "utt_p1",
                    "meeting_id": "mtg_past",
                }
            ]
        )
        return {"ok": True, "summary": "발언", "items": ms, "evidence": [m["id"] for m in ms]}

    def save(
        session: Any, team_id: str, meeting_id: str, body: str, utterance_ids: list[str]
    ) -> dict[str, Any]:
        log.append(SAVE)
        return {"ok": True, "summary": "저장", "evidence": ["rdoc_1"]}

    return {
        OVERVIEW: _tool(OVERVIEW, overview),
        QUESTIONS: _tool(QUESTIONS, unresolved),
        RECENT: _tool(RECENT, recent_meetings),
        SEARCH: _tool(SEARCH, search),
        SAVE: _tool(SAVE, save),
    }


def invoke(
    tools: dict[str, Tool],
    writer: FakeWriter,
    *,
    session: Session,
    team_id: str,
    meeting: str | None,
    budget: CallBudget | None = None,
) -> Any:
    sub = make_subagent(writer)
    box = Toolbox(
        tools,
        session,
        budget or CallBudget(),
        allowed=sub.tools,
        scope=RunScope(team_id=team_id, meeting_id=meeting),
    )
    return sub.build(box).invoke({"request": INTELLIGENCE_COMPLETED})["outcome"]


def test_it_is_collected_woken_by_intelligence_completed_and_reads_only_its_list() -> None:
    assert collect_subagents()["research"] is SUBAGENT
    assert SUBAGENT.triggers == (INTELLIGENCE_COMPLETED,)
    assert set(SUBAGENT.tools) == {OVERVIEW, QUESTIONS, RECENT, SEARCH, SAVE}


def test_a_meeting_with_questions_gets_a_document_and_one_l2_proposal(session, team) -> None:
    calls: list[str] = []
    writer = FakeWriter()

    outcome = invoke(
        tools_for(calls=calls),
        writer,
        session=session,
        team_id=team["team"],
        meeting=team["meeting"],
    )

    assert outcome.result.ok is True
    assert outcome.result.evidence == ["rdoc_1"]
    [proposal] = outcome.proposed
    assert (proposal.tool, proposal.level, proposal.arguments) == (
        SHARE,
        "L2",
        {"document_id": "rdoc_1"},
    )
    assert calls == [OVERVIEW, QUESTIONS, SEARCH, SAVE]
    assert writer.written[0]["questions"] == ["배포는 언제죠?"]


def test_no_questions_means_no_document_and_no_proposal(session, team) -> None:
    calls: list[str] = []

    outcome = invoke(
        tools_for(questions=[], calls=calls),
        FakeWriter(),
        session=session,
        team_id=team["team"],
        meeting=team["meeting"],
    )

    assert outcome.result.ok is True
    assert outcome.proposed == []
    assert SAVE not in calls


def test_questions_with_empty_text_count_as_none(session, team) -> None:
    outcome = invoke(
        tools_for(questions=[{"title": "질문", "body": "", "id": "utt_q1"}]),
        FakeWriter(),
        session=session,
        team_id=team["team"],
        meeting=team["meeting"],
    )

    assert outcome.proposed == []


def test_no_terms_still_writes_from_the_questions(session, team) -> None:
    calls: list[str] = []
    writer = FakeWriter(terms=[])

    outcome = invoke(
        tools_for(calls=calls),
        writer,
        session=session,
        team_id=team["team"],
        meeting=team["meeting"],
    )

    assert SEARCH not in calls
    assert writer.written[0]["matches"] == []
    assert len(outcome.proposed) == 1


def test_a_writer_failure_ends_without_a_proposal(session, team) -> None:
    calls: list[str] = []

    outcome = invoke(
        tools_for(calls=calls),
        FakeWriter(fail=True),
        session=session,
        team_id=team["team"],
        meeting=team["meeting"],
    )

    assert outcome.result.ok is False
    assert outcome.proposed == []
    assert SAVE not in calls


def test_the_trigger_path_stays_within_eight_calls(session, team) -> None:
    budget = CallBudget()

    invoke(
        tools_for(),
        FakeWriter(terms=["a", "b", "c", "d", "e"]),
        session=session,
        team_id=team["team"],
        meeting=team["meeting"],
        budget=budget,
    )

    assert budget.used <= 8


def test_a_chat_run_researches_the_latest_analysed_meeting(session, team) -> None:
    calls: list[str] = []
    recent = [
        {"title": "예정 회의", "body": "", "meeting_id": "mtg_future", "status": "scheduled"},
        {"title": "지난 회의", "body": "", "meeting_id": team["meeting"], "status": "complete"},
    ]
    budget = CallBudget()

    outcome = invoke(
        tools_for(recent=recent, calls=calls),
        FakeWriter(),
        session=session,
        team_id=team["team"],
        meeting=None,
        budget=budget,
    )

    assert calls == [RECENT, OVERVIEW, QUESTIONS, SEARCH, SAVE]
    assert len(outcome.proposed) == 1
    assert budget.used == 6  # the refused overview counts
    assert budget.used <= 10


def test_a_chat_run_with_no_analysed_meeting_says_so(session, team) -> None:
    calls: list[str] = []

    outcome = invoke(
        tools_for(recent=[], calls=calls),
        FakeWriter(),
        session=session,
        team_id=team["team"],
        meeting=None,
    )

    assert outcome.result.ok is False
    assert outcome.result.summary == "조사할 회의가 없습니다."
    assert calls == [RECENT]
    assert outcome.proposed == []


def test_matches_are_deduplicated_across_terms(session, team) -> None:
    writer = FakeWriter(terms=["배포", "금요일"])

    invoke(tools_for(), writer, session=session, team_id=team["team"], meeting=team["meeting"])

    assert [m.utterance_id for m in writer.written[0]["matches"]] == ["utt_p1"]


def test_a_privacy_refusal_from_the_writer_fails_the_run(session, team) -> None:
    class Refusing(FakeWriter):
        def write(self, **kwargs: Any) -> str:
            raise PrivacyViolationError("outbound text refused")

    with pytest.raises(PrivacyViolationError):
        invoke(
            tools_for(),
            Refusing(),
            session=session,
            team_id=team["team"],
            meeting=team["meeting"],
        )
