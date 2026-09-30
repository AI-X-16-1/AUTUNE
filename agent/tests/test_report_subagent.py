"""The Report subagent end to end over mock tools (no database)."""

from __future__ import annotations

from typing import Any

from autune_agent.main import CallBudget, Tool, Toolbox
from autune_agent.results import SubagentResult
from autune_agent.subagents.report import SUBAGENT
from autune_agent.subagents.report.graph import (
    ACTIONS_TOOL,
    GAPS_TOOL,
    LINKS_TOOL,
    PUBLISH_TOOL,
    REVIEW_TOOL,
)
from autune_agent.testing import mock_tool

SESSION: Any = object()
ACTIONS = {
    "ok": True,
    "summary": "확정 1건.",
    "items": [{"title": "API 스펙", "body": "백엔드 · 10/2"}],
}
REVIEW = {"ok": True, "summary": "결정 확인 대기 1건.", "items": [{"title": "결정 확인 대기"}]}
GAPS = {"ok": True, "summary": "논의된 토픽: 결제 수단", "items": []}
LINKS = {
    "ok": True,
    "summary": "",
    "items": [{"title": "x", "meeting_title": "지난 회의", "date": "9/22"}],
}


def _run(request: str, tools: dict[str, Any], budget: CallBudget | None = None) -> SubagentResult:
    box = Toolbox(tools, SESSION, budget or CallBudget(), allowed=SUBAGENT.tools)
    out = SUBAGENT.build(box).invoke({"request": request})
    return SubagentResult.model_validate(out["outcome"])


def _all_tools() -> dict[str, Any]:
    return {
        ACTIONS_TOOL: mock_tool(ACTIONS_TOOL, ACTIONS),
        REVIEW_TOOL: mock_tool(REVIEW_TOOL, REVIEW),
        GAPS_TOOL: mock_tool(GAPS_TOOL, GAPS),
        LINKS_TOOL: mock_tool(LINKS_TOOL, LINKS),
    }


def test_a_finished_meeting_becomes_one_l1_proposal_to_e() -> None:
    outcome = _run("meeting mtg_ab12cd analysis completed", _all_tools())

    assert outcome.result.ok is True
    [action] = outcome.proposed
    assert (action.level, action.tool, action.kind) == ("L1", PUBLISH_TOOL, "meeting_report")
    assert action.arguments["meeting_id"] == "mtg_ab12cd"
    assert action.arguments["pending_review"] is True
    assert action.arguments["body_markdown"].startswith("✅ 확정된 액션 아이템")
    assert "team_id" not in action.arguments  # the run's scope fills it (RUN_SCOPE)


def test_no_meeting_id_means_no_proposal() -> None:
    outcome = _run("리포트 써줘", _all_tools())

    assert outcome.result.ok is False
    assert outcome.proposed == []


def test_a_missing_tool_drops_its_section_and_the_report_still_goes() -> None:
    tools = _all_tools()
    del tools[GAPS_TOOL]

    outcome = _run("mtg_ab12cd", tools)

    assert "💬" not in outcome.proposed[0].arguments["body_markdown"]


def test_at_most_four_tool_calls() -> None:
    budget = CallBudget()
    _run("mtg_ab12cd", _all_tools(), budget)
    assert budget.used == 4


def test_a_korean_particle_after_the_id_still_finds_the_meeting() -> None:
    outcome = _run("mtg_ab12cd의 리포트 다시 만들어줘", _all_tools())

    assert outcome.proposed[0].arguments["meeting_id"] == "mtg_ab12cd"


def test_two_different_meeting_ids_are_refused_not_guessed() -> None:
    outcome = _run("mtg_aaa1 와 mtg_bbb2 비교", _all_tools())

    assert outcome.result.ok is False
    assert outcome.proposed == []


def test_an_optional_tool_that_raises_drops_only_its_section() -> None:
    """A bug in C's or D's tool must not cost the meeting its confirmed items."""

    def broken(_session: object, **_kw: object) -> dict[str, Any]:
        raise RuntimeError("gap tool bug")

    tools = _all_tools()
    tools[GAPS_TOOL] = Tool(name=GAPS_TOOL, description="Use this in tests.", fn=broken)

    outcome = _run("mtg_ab12cd", tools)

    body = outcome.proposed[0].arguments["body_markdown"]
    assert "💬" not in body and "✅ 확정된 액션 아이템" in body


def test_an_unknown_meeting_is_a_failure_with_no_proposal() -> None:
    missing = {"ok": False, "reason": "no meeting", "summary": "회의를 찾을 수 없습니다."}
    tools = _all_tools()
    tools[ACTIONS_TOOL] = mock_tool(ACTIONS_TOOL, missing)
    tools[REVIEW_TOOL] = mock_tool(REVIEW_TOOL, missing)

    outcome = _run("mtg_ab12cd", tools)

    assert outcome.result.ok is False
    assert outcome.proposed == []


def test_an_unregistered_tool_is_not_called_and_spends_nothing() -> None:
    tools = _all_tools()
    del tools[GAPS_TOOL], tools[LINKS_TOOL]
    budget = CallBudget()

    _run("mtg_ab12cd", tools, budget)

    assert budget.used == 2


def test_the_allow_list_is_exactly_the_four_reads() -> None:
    assert SUBAGENT.name == "report"
    assert set(SUBAGENT.tools) == {ACTIONS_TOOL, REVIEW_TOOL, GAPS_TOOL, LINKS_TOOL}
    assert "extraction.unresolved_questions" not in SUBAGENT.tools
    assert "extraction.open_action_items" not in SUBAGENT.tools
    assert SUBAGENT.description.startswith("Use this")
    # E refuses a report already posted, so the subagent must not promise a resend.
    assert "resend" not in SUBAGENT.description
