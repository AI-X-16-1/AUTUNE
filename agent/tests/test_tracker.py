"""The Tracker subagent ("할 일 챙김", #856) against a mock tool.

The mock returns what B's ``stalled_action_items`` returns, extra fields
included, so a change to B's shape shows up here. ``test_tracker_approval.py``
runs the same subagent on B's real tools through to an approval.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from autune_agent.main import CallBudget, RunScope, collect_subagents
from autune_agent.main.pending import arguments_ok, scope_for
from autune_agent.main.registry import Toolbox
from autune_agent.main.subagents import Periodic
from autune_agent.subagents.tracker import SUBAGENT, graph, plan
from autune_agent.subagents.tracker.graph import SET_DUE_DATE, STALLED
from autune_agent.testing import mock_tool
from autune_contracts import INTELLIGENCE_COMPLETED

SESSION: Any = object()
SCOPE = RunScope(team_id="team_a")
WEDNESDAY = date(2026, 10, 7)


@pytest.fixture(autouse=True)
def _a_wednesday(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph, "_today", lambda: WEDNESDAY)


def confirmed(item_id: str, *ways: str, flagged: bool = False) -> dict[str, Any]:
    return {
        "title": f"{item_id} 할 일",
        "body": "박지영 · 2026-10-01 · 기한 지남 · todo",
        "score": 0.9 if "overdue" in ways else 0.7,
        "id": item_id,
        "meeting_id": "mtg_a",
        "overdue": "overdue" in ways,
        "needs_reassignment": flagged,
        "stalled": list(ways),
        "carried_meetings": 3 if "carried" in ways else 0,
    }


def waiting(item_id: str, days: int = 4) -> dict[str, Any]:
    """An item nobody has confirmed, as B gives it: no text, no holder, no date."""
    return {
        "title": "액션아이템 확인 대기",
        "body": f"{days}일째 확인 대기",
        "score": 0.5,
        "id": item_id,
        "meeting_id": "mtg_a",
        "stalled": ["unconfirmed"],
        "waiting_days": days,
    }


def stalled(*rows: dict[str, Any], ok: bool = True, truncated: bool = False) -> dict[str, Any]:
    return {
        "ok": ok,
        "reason": None if ok else "no team",
        "summary": "멈춰 있는 액션아이템",
        "items": list(rows),
        "evidence": [],
        "truncated": truncated,
    }


def invoke(result: dict[str, Any]) -> Any:
    tools = {STALLED: mock_tool(STALLED, result)}
    box = Toolbox(tools, SESSION, CallBudget(), allowed=SUBAGENT.tools, scope=SCOPE)
    return SUBAGENT.build(box).invoke({"request": "밀린 할 일 정리해줘"})["outcome"]


# --- what is proposed --------------------------------------------------------------------


def test_a_late_item_gets_its_due_date_moved_a_week_on() -> None:
    (proposal,) = invoke(stalled(confirmed("act_late", "overdue"))).proposed

    assert (proposal.level, proposal.tool, proposal.kind) == ("L2", SET_DUE_DATE, plan.MOVE)
    assert proposal.arguments == {"action_item_id": "act_late", "due_date": "2026-10-14"}
    assert "10월 14일(수)" in proposal.rationale


def test_a_late_item_that_is_also_long_carried_gets_the_same_one_proposal() -> None:
    (proposal,) = invoke(stalled(confirmed("act_both", "overdue", "carried"))).proposed

    assert (proposal.tool, proposal.kind) == (SET_DUE_DATE, plan.MOVE)
    assert proposal.arguments == {"action_item_id": "act_both", "due_date": "2026-10-14"}


def test_a_long_carried_item_that_is_not_late_gets_no_proposal_yet_and_is_counted() -> None:
    # Closing waits for module B to mark a close apart from finished work
    # (the user, 2026-10-07); until then no write is proposed for it.
    outcome = invoke(stalled(confirmed("act_late", "overdue"), confirmed("act_carried", "carried")))

    assert [p.arguments["action_item_id"] for p in outcome.proposed] == ["act_late"]
    assert {p.tool for p in outcome.proposed} == {SET_DUE_DATE}
    assert f"{plan.CARRIED_WORDS} 1건은 제안하지 않습니다" in outcome.result.summary


def test_every_proposal_passes_the_queues_argument_rule_and_names_no_team() -> None:
    outcome = invoke(stalled(confirmed("act_late", "overdue"), confirmed("act_carried", "carried")))

    for proposal in outcome.proposed:
        assert arguments_ok(proposal.arguments)
        assert "team_id" not in proposal.arguments, "team_id is the run's"
        assert proposal.evidence == [proposal.arguments["action_item_id"]]


def test_an_item_nobody_confirmed_is_never_proposed_and_never_quoted() -> None:
    outcome = invoke(stalled(confirmed("act_late", "overdue"), waiting("act_wait")))

    assert [p.arguments["action_item_id"] for p in outcome.proposed] == ["act_late"]
    assert [i.model_extra["id"] for i in outcome.result.items] == ["act_late"]
    assert outcome.result.evidence == ["act_late"]
    assert "확인 대기" not in outcome.result.summary


def test_a_row_marked_unconfirmed_is_skipped_whatever_else_it_says() -> None:
    # B never says both today. If its shape ever did, the mark that the item
    # is unconfirmed is the one that counts.
    row = {**waiting("act_wait"), "stalled": ["unconfirmed", "overdue"], "overdue": True}

    assert invoke(stalled(row)).proposed == []


def test_only_unconfirmed_items_proposes_nothing() -> None:
    outcome = invoke(stalled(waiting("act_wait1"), waiting("act_wait2")))

    assert outcome.result.ok is True
    assert outcome.proposed == []
    assert "제안하지 않습니다" in outcome.result.summary


def test_an_item_flagged_for_reassignment_is_left_to_that_flag() -> None:
    outcome = invoke(stalled(confirmed("act_gone", "overdue", flagged=True)))

    assert outcome.proposed == []


def test_at_most_five_proposals_and_the_cut_is_said() -> None:
    rows = [confirmed(f"act_{n}", "overdue") for n in range(5)]

    outcome = invoke(stalled(*rows, truncated=True))

    assert len(outcome.proposed) == plan.MAX_PROPOSALS == 5
    assert outcome.result.truncated is True, "five is not read as all"


def test_the_rule_itself_stops_at_five_whatever_it_is_handed() -> None:
    # A tool result is already cut to five; the rule does not lean on that.
    late = [
        plan.Stalled(action_item_id=f"act_{n}", title="할 일", overdue=True, carried=False)
        for n in range(7)
    ]

    moves = plan.plan_moves(late, today=WEDNESDAY)

    assert [m.item.action_item_id for m in moves] == [f"act_{n}" for n in range(5)]


def test_the_summary_counts_proposals_and_never_says_carried_alone() -> None:
    outcome = invoke(
        stalled(
            confirmed("act_both", "overdue", "carried"),
            confirmed("act_late", "overdue"),
            confirmed("act_carried", "carried"),
        )
    )

    summary = outcome.result.summary
    assert "기한 옮기기 제안 2건" in summary
    texts = (
        summary,
        *(p.rationale for p in outcome.proposed),
        *(p.title for p in outcome.proposed),
    )
    for text in texts:
        assert "이월" not in text.replace(plan.CARRIED_WORDS, ""), "lsh2217 on #856"


def test_an_unreadable_team_stops_without_a_proposal() -> None:
    outcome = invoke(stalled(ok=False))

    assert outcome.result.ok is False
    assert outcome.result.reason == "no team"
    assert outcome.proposed == []


def test_nothing_stalled_is_an_answer_not_a_failure() -> None:
    outcome = invoke(stalled())

    assert outcome.result.ok is True
    assert outcome.proposed == [] and outcome.result.items == []


# --- the new date ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "moved_to"),
    [
        pytest.param(date(2026, 10, 7), date(2026, 10, 14), id="wednesday-to-wednesday"),
        pytest.param(
            date(2026, 10, 3), date(2026, 10, 12), id="saturday-lands-on-saturday-so-monday"
        ),
        pytest.param(date(2026, 10, 4), date(2026, 10, 12), id="sunday-lands-on-sunday-so-monday"),
        pytest.param(date(2026, 12, 29), date(2027, 1, 5), id="across-the-year"),
    ],
)
def test_the_new_date_is_a_week_on_and_never_a_weekend(today: date, moved_to: date) -> None:
    assert plan.new_due_date(today) == moved_to
    assert moved_to.weekday() < 5


# --- how it is declared ------------------------------------------------------------------


def test_it_is_the_sixth_subagent_and_wakes_weekly_and_after_a_meeting() -> None:
    assert collect_subagents()["tracker"] is SUBAGENT
    assert SUBAGENT.period == Periodic(hours=168)
    assert INTELLIGENCE_COMPLETED in SUBAGENT.triggers
    assert SUBAGENT.proposals_per == "team", "a run replaces the team's earlier waiting cards"
    assert SUBAGENT.answers_lookups is False, "it proposes; a question must not wake it"


def test_it_reads_one_tool_and_holds_no_write() -> None:
    assert SUBAGENT.tools == (STALLED,)
    assert SET_DUE_DATE not in SUBAGENT.tools


def test_its_proposals_go_to_the_manager_with_no_scope_of_their_own() -> None:
    assert scope_for("tracker") == "any"
