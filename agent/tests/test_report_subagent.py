"""The Report subagent end to end over mock tools (no database)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from autune_agent.main import BudgetExceededError, CallBudget, RunScope, Tool, Toolbox
from autune_agent.main.pending import arguments_ok
from autune_agent.main.registry import collect_tools
from autune_agent.main.subagents import TRIGGER_EVENTS
from autune_agent.results import SubagentResult
from autune_agent.subagents.report import SUBAGENT
from autune_agent.subagents.report.graph import (
    ACTIONS_TOOL,
    AWAITING_TOOL,
    CORRECTION_ACTION,
    DRAFT_ACTION,
    GAPS_TOOL,
    LINKS_TOOL,
    PUBLISH_ACTION,
    REVIEW_TOOL,
)
from autune_agent.testing import mock_tool

TEAM = "team_a"
MEETING = "mtg_ab12cd"
EVENT = "autune.intelligence.completed"
CHANGED = "autune.intelligence.meeting_report_changed"

ACTIONS = {
    "ok": True,
    "summary": "확정 1건.",
    "items": [{"title": "API 스펙", "body": "백엔드 · 10/2"}],
}
REVIEW = {"ok": True, "summary": "결정 확인 대기 1건.", "items": [{"title": "결정 확인 대기"}]}
GAPS = {"ok": True, "summary": "이 회의에 열린 갭이 없습니다.", "items": []}
LINKS = {
    "ok": True,
    "summary": "",
    "items": [{"title": "x", "meeting_title": "지난 회의", "date": "9/22"}],
}


class _Session:
    """What the Toolbox's scope check reads: a meeting and its team."""

    def __init__(self, meetings: dict[str, str]) -> None:
        self.meetings = meetings

    def get(self, _model: object, ident: str) -> Any:
        team = self.meetings.get(ident)
        return None if team is None else SimpleNamespace(team_id=team)


def _run(
    request: str,
    tools: dict[str, Any],
    *,
    scope_meeting: str | None = None,
    budget: CallBudget | None = None,
) -> SubagentResult:
    session = _Session({MEETING: TEAM, "mtg_aaa1": TEAM, "mtg_bbb2": TEAM})
    box = Toolbox(
        tools,
        session,  # type: ignore[arg-type]
        budget or CallBudget(),
        scope=RunScope(team_id=TEAM, meeting_id=scope_meeting),
        allowed=SUBAGENT.tools,
    )
    out = SUBAGENT.build(box).invoke({"request": request})
    return SubagentResult.model_validate(out["outcome"])


def _all_tools() -> dict[str, Any]:
    return {
        ACTIONS_TOOL: mock_tool(ACTIONS_TOOL, ACTIONS),
        REVIEW_TOOL: mock_tool(REVIEW_TOOL, REVIEW),
        GAPS_TOOL: mock_tool(GAPS_TOOL, GAPS),
        LINKS_TOOL: mock_tool(LINKS_TOOL, LINKS),
    }


# --- woken by the pipeline --------------------------------------------------------


def test_it_wakes_on_the_analysis_finishing_and_on_a_persons_edit() -> None:
    assert SUBAGENT.triggers == (EVENT, CHANGED)
    assert set(SUBAGENT.triggers) <= set(TRIGGER_EVENTS)


def test_a_finished_meeting_becomes_a_draft_at_l1_and_a_post_at_l2() -> None:
    outcome = _run(EVENT, _all_tools(), scope_meeting=MEETING)

    assert outcome.result.ok is True
    draft, post = outcome.proposed
    assert (draft.tool, draft.level, draft.kind) == (DRAFT_ACTION, "L1", "meeting_report_draft")
    assert (post.tool, post.level, post.kind) == (PUBLISH_ACTION, "L2", "meeting_report_post")
    # The run's scope carries the meeting and the team (#449, #509); the model sets neither.
    assert set(draft.arguments) == {"body_markdown", "pending_review", "draft_id"}
    assert post.arguments == {"draft_id": draft.arguments["draft_id"]}
    assert draft.arguments["pending_review"] is True
    assert draft.arguments["body_markdown"].startswith("✅ 확정된 액션 아이템")


# --- a person edited the draft (#674) ---------------------------------------------


def _awaiting(draft_id: str | None) -> dict[str, Any]:
    items = (
        []
        if draft_id is None
        else [{"title": "리포트 초안", "id": MEETING, "kind": "draft", "draft_id": draft_id}]
    )
    return {"ok": True, "summary": "", "items": items}


def _awaiting_correction(correction_id: str) -> dict[str, Any]:
    item = {
        "title": "리포트 수정본",
        "id": MEETING,
        "kind": "correction",
        "correction_id": correction_id,
    }
    return {"ok": True, "summary": "", "items": [item]}


def test_an_edit_is_proposed_for_approval_again_without_rendering() -> None:
    tools = {**_all_tools(), AWAITING_TOOL: mock_tool(AWAITING_TOOL, _awaiting("rdr_edited"))}
    budget = CallBudget()

    outcome = _run(CHANGED, tools, scope_meeting=MEETING, budget=budget)

    [post] = outcome.proposed
    assert (post.tool, post.level, post.kind) == (PUBLISH_ACTION, "L2", "meeting_report_post")
    # The edited draft's id, so the approval posts exactly that text (#570).
    assert post.arguments == {"draft_id": "rdr_edited"}
    assert arguments_ok(post.arguments)
    assert budget.used == 1  # E's read only; nothing from B, C or D is read again


def test_a_correction_is_proposed_for_approval_with_its_id() -> None:
    """After the post a person's change is a correction; it is approved like a post (#674)."""
    tools = {AWAITING_TOOL: mock_tool(AWAITING_TOOL, _awaiting_correction("rcr_fix"))}

    outcome = _run(CHANGED, tools, scope_meeting=MEETING)

    [post] = outcome.proposed
    assert (post.tool, post.level, post.kind) == (
        CORRECTION_ACTION,
        "L2",
        "meeting_report_correction_post",
    )
    assert post.arguments == {"correction_id": "rcr_fix"} and arguments_ok(post.arguments)


def test_the_correction_action_is_one_e_actually_ships() -> None:
    from autune_intelligence import tools as e_tools

    assert CORRECTION_ACTION.split(".", 1)[1] in {a.__name__ for a in e_tools.ACTIONS}
    assert CORRECTION_ACTION.split(".", 1)[1] not in {a.__name__ for a in e_tools.L1_ACTIONS}


def test_nothing_awaiting_proposes_nothing() -> None:
    """Posted, or replaced by a rerun that proposed its own post, before this run woke."""
    tools = {AWAITING_TOOL: mock_tool(AWAITING_TOOL, _awaiting(None))}

    outcome = _run(CHANGED, tools, scope_meeting=MEETING)

    assert outcome.result.ok is False and outcome.proposed == []


def test_an_edit_on_another_teams_meeting_proposes_nothing() -> None:
    missing = {"ok": False, "reason": "meeting not found", "summary": "회의를 찾을 수 없습니다."}
    tools = {AWAITING_TOOL: mock_tool(AWAITING_TOOL, missing)}

    outcome = _run(CHANGED, tools, scope_meeting=MEETING)

    assert outcome.result.ok is False and outcome.proposed == []


def test_the_awaiting_read_is_a_tool_e_actually_ships() -> None:
    assert AWAITING_TOOL in collect_tools(["intelligence"])


# --- asked in chat ----------------------------------------------------------------


def test_a_chat_request_names_the_meeting_and_the_proposals_carry_it() -> None:
    outcome = _run(f"{MEETING} 리포트 만들어줘", _all_tools())

    draft, post = outcome.proposed
    assert draft.arguments["meeting_id"] == MEETING
    assert post.arguments == {"meeting_id": MEETING, "draft_id": draft.arguments["draft_id"]}


def test_a_korean_particle_after_the_id_still_finds_the_meeting() -> None:
    outcome = _run(f"{MEETING}의 리포트 만들어줘", _all_tools())

    assert outcome.proposed[0].arguments["meeting_id"] == MEETING


def _per_meeting_tools(seen: list[str]) -> dict[str, Any]:
    """B's two reads with their real signature: ``meeting_id`` is required, so
    the Toolbox fills it from the run's scope or refuses the call."""

    def actions(_session: object, meeting_id: str) -> dict[str, Any]:
        seen.append(meeting_id)
        return ACTIONS

    def review(_session: object, meeting_id: str) -> dict[str, Any]:
        return REVIEW

    return {
        ACTIONS_TOOL: Tool(name=ACTIONS_TOOL, description="Use this in tests.", fn=actions),
        REVIEW_TOOL: Tool(name=REVIEW_TOOL, description="Use this in tests.", fn=review),
    }


def test_asked_on_a_meetings_screen_it_reports_the_runs_meeting() -> None:
    """ "리포트 써줘" names no id; the run about that meeting already carries it."""
    seen: list[str] = []

    outcome = _run("리포트 써줘", _per_meeting_tools(seen), scope_meeting=MEETING)

    assert seen == [MEETING]
    draft, post = outcome.proposed
    assert set(draft.arguments) == {"body_markdown", "pending_review", "draft_id"}
    assert post.arguments == {"draft_id": draft.arguments["draft_id"]}


def test_the_post_is_pinned_to_this_runs_draft() -> None:
    """E posts only the draft the approved proposal names, so each run's id is its own."""
    first = _run(EVENT, _all_tools(), scope_meeting=MEETING)
    second = _run(EVENT, _all_tools(), scope_meeting=MEETING)

    ids = [run.proposed[1].arguments["draft_id"] for run in (first, second)]
    assert ids[0] != ids[1]
    # Plan mode queues an L2 row only when its arguments are ids and short scalars.
    assert all(arguments_ok(run.proposed[1].arguments) for run in (first, second))
    assert all(i.startswith("rdr_") for i in ids)


def test_a_chat_request_about_no_meeting_proposes_nothing() -> None:
    outcome = _run("리포트 써줘", _per_meeting_tools([]))

    assert outcome.result.ok is False
    assert outcome.proposed == []


def test_running_out_of_tool_calls_is_not_swallowed_as_a_missing_section() -> None:
    """The budget is the run's stop; only a tool's own failure drops a section."""
    with pytest.raises(BudgetExceededError):
        _run(EVENT, _all_tools(), scope_meeting=MEETING, budget=CallBudget(limit=2))


def test_two_different_meeting_ids_are_refused_not_guessed() -> None:
    outcome = _run("mtg_aaa1 와 mtg_bbb2 비교", _all_tools())

    assert outcome.result.ok is False
    assert outcome.proposed == []


def test_another_teams_meeting_reads_as_missing() -> None:
    """The Toolbox's scope check answers "meeting not found"; no report is made."""
    outcome = _run("mtg_zzz9 리포트", _all_tools())

    assert outcome.result.ok is False
    assert outcome.proposed == []


# --- robustness -------------------------------------------------------------------


def test_a_missing_tool_drops_its_section_and_the_report_still_goes() -> None:
    tools = _all_tools()
    del tools[GAPS_TOOL]

    outcome = _run(EVENT, tools, scope_meeting=MEETING)

    assert "열린 갭" not in outcome.proposed[0].arguments["body_markdown"]


def test_an_optional_tool_that_raises_drops_only_its_section() -> None:
    """A bug in C's or D's tool must not cost the meeting its confirmed items."""

    def broken(_session: object, **_kw: object) -> dict[str, Any]:
        raise RuntimeError("gap tool bug")

    tools = _all_tools()
    tools[GAPS_TOOL] = Tool(name=GAPS_TOOL, description="Use this in tests.", fn=broken)

    outcome = _run(EVENT, tools, scope_meeting=MEETING)

    body = outcome.proposed[0].arguments["body_markdown"]
    assert "열린 갭" not in body and "✅ 확정된 액션 아이템" in body


def test_an_unknown_meeting_is_a_failure_with_no_proposal() -> None:
    missing = {"ok": False, "reason": "no meeting", "summary": "회의를 찾을 수 없습니다."}
    tools = _all_tools()
    tools[ACTIONS_TOOL] = mock_tool(ACTIONS_TOOL, missing)
    tools[REVIEW_TOOL] = mock_tool(REVIEW_TOOL, missing)

    outcome = _run(EVENT, tools, scope_meeting=MEETING)

    assert outcome.result.ok is False
    assert outcome.proposed == []


@pytest.mark.parametrize("registered", [4, 2])
def test_it_calls_only_registered_tools(registered: int) -> None:
    tools = _all_tools()
    if registered == 2:
        del tools[GAPS_TOOL], tools[LINKS_TOOL]
    budget = CallBudget()

    _run(EVENT, tools, scope_meeting=MEETING, budget=budget)

    assert budget.used == registered


def test_the_gap_read_is_a_tool_c_actually_ships() -> None:
    """An unregistered name is skipped silently, so a wrong one would just lose the section."""
    assert GAPS_TOOL in collect_tools(["gap"])


def test_the_allow_list_is_exactly_the_five_reads() -> None:
    assert SUBAGENT.name == "report"
    assert set(SUBAGENT.tools) == {ACTIONS_TOOL, REVIEW_TOOL, GAPS_TOOL, LINKS_TOOL, AWAITING_TOOL}
    assert "extraction.unresolved_questions" not in SUBAGENT.tools
    assert "extraction.open_action_items" not in SUBAGENT.tools
    assert SUBAGENT.description.startswith("Use this")
    # E refuses a report already posted, so the subagent must not promise a resend.
    assert "resend" not in SUBAGENT.description
