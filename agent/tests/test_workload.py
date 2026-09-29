"""The Workload subagent against mock tools (agent-layer.md section 14: by 10/5).

The mocks return what B's ``workload_by_owner`` and ``person_action_items``
return, counts as fields included, so a change to B's shape shows up here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from autune_agent.main import CallBudget, collect_subagents, run
from autune_agent.main.registry import Tool, Toolbox
from autune_agent.subagents.workload import SUBAGENT, plan
from autune_agent.subagents.workload.graph import BUSY, ITEMS, LOAD, REASSIGN
from autune_agent.testing import FakeRouter, mock_tool

SESSION: Any = object()


def person(
    uid: str, name: str, open_: int, overdue: int = 0, done: int = 0, state: str = ""
) -> dict[str, Any]:
    return {
        "title": name,
        "body": f"진행 중 {open_} · 기한 지남 {overdue}",
        "score": float(open_ + overdue),
        "id": uid,
        "open": open_,
        "overdue": overdue,
        "done": done,
        "state": state,
    }


def load(*rows: dict[str, Any], ok: bool = True) -> dict[str, Any]:
    return {
        "ok": ok,
        "reason": None if ok else "no team",
        "summary": "업무량",
        "items": list(rows),
        "evidence": [],
    }


def items(*ids: str, late: tuple[str, ...] = (), flagged: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "ok": True,
        "summary": "할 일",
        "items": [
            {
                "title": f"{i} 할 일",
                "body": "",
                "score": 0.9 if i in late else 0.5,
                "id": i,
                "overdue": i in late,
                "needs_reassignment": i in flagged,
            }
            for i in ids
        ],
        "evidence": [],
    }


def tools_for(
    workload: dict[str, Any],
    by_person: Mapping[str, dict[str, Any]],
    busy: Mapping[str, float | None] | None = None,
) -> tuple[dict[str, Tool], list[dict[str, Any]]]:
    """Mock tools. ``busy`` is each member's ``busy_share`` (``None`` for an
    unreadable calendar); leaving it out is a team with no calendar connection,
    whose tool is not registered at all."""
    calls: list[dict[str, Any]] = []

    def person_items(_session: Any, **arguments: Any) -> dict[str, Any]:
        calls.append(arguments)
        return by_person.get(arguments["user_id"], items())

    def busy_hours(_session: Any, **arguments: Any) -> dict[str, Any]:
        calls.append(arguments)
        assert busy is not None
        return {
            "ok": True,
            "summary": "일정",
            "items": [
                {"title": uid, "id": uid, "busy_share": busy.get(uid), "busy_hours": None}
                for uid in arguments["user_ids"]
            ],
            "evidence": [],
        }

    tools = {
        LOAD: mock_tool(LOAD, workload),
        ITEMS: Tool(name=ITEMS, description="Use this in tests.", fn=person_items),
        "intelligence.quality_score": mock_tool("intelligence.quality_score", load()),
    }
    if busy is not None:
        tools[BUSY] = Tool(name=BUSY, description="Use this in tests.", fn=busy_hours)
    return tools, calls


def invoke(tools: dict[str, Tool], budget: CallBudget | None = None) -> Any:
    box = Toolbox(tools, SESSION, budget or CallBudget(), allowed=SUBAGENT.tools)
    return SUBAGENT.build(box).invoke({"request": "업무 몰린 사람 있어?"})["outcome"]


TEAM = (
    person("user_a", "김과부", 6, overdue=2, state="loaded"),
    person("user_b", "이보통", 2),
    person("user_c", "박여유", 0, done=4, state="free"),
)


def test_a_loaded_persons_most_urgent_items_go_to_someone_free() -> None:
    tools, calls = tools_for(
        load(*TEAM), {"user_a": items("act_1", "act_2", "act_3", late=("act_1", "act_2"))}
    )

    outcome = invoke(tools)

    assert [
        (p.arguments["action_item_id"], p.arguments["assignee_id"]) for p in outcome.proposed
    ] == [
        ("act_1", "user_c"),
        ("act_2", "user_c"),
    ]
    assert calls == [{"user_id": "user_a"}]
    assert "재배정 제안 2건" in outcome.result.summary
    assert "팀 캘린더 없이" in outcome.result.summary
    assert outcome.result.evidence == ["act_1", "act_2"]


def test_every_proposal_is_an_l2_reassignment_the_run_scopes() -> None:
    tools, _ = tools_for(load(*TEAM), {"user_a": items("act_1")})

    (proposal,) = invoke(tools).proposed

    assert (proposal.level, proposal.tool, proposal.kind) == (
        "L2",
        REASSIGN,
        "reassign_action_item",
    )
    assert set(proposal.arguments) == {"action_item_id", "assignee_id"}, "team_id is the run's"
    assert "김과부" in proposal.rationale and "박여유" in proposal.rationale


def test_nobody_loaded_proposes_nothing_and_reads_no_items() -> None:
    tools, calls = tools_for(
        load(person("user_b", "이보통", 2), person("user_c", "박여유", 0, state="free")), {}
    )

    outcome = invoke(tools)

    assert outcome.result.ok is True
    assert outcome.proposed == []
    assert "몰린 사람이 없어" in outcome.result.summary
    assert calls == []


def test_nobody_to_take_work_proposes_nothing_and_spends_one_call() -> None:
    budget = CallBudget()
    tools, calls = tools_for(
        load(
            person("user_a", "김과부", 6, 2, state="loaded"),
            person("user_b", "이바쁨", 5, overdue=1),
        ),
        {"user_a": items("act_1")},
    )

    outcome = invoke(tools, budget)

    assert outcome.proposed == []
    assert "넘겨받을 수 있는 사람이 없어" in outcome.result.summary
    assert (budget.used, calls) == (1, [])


def test_one_taker_is_not_turned_into_the_next_pile() -> None:
    tools, _ = tools_for(
        load(
            person("user_a", "김과부", 6, 2, state="loaded"),
            person("user_d", "최과부", 5, 2, state="loaded"),
            person("user_c", "박여유", 0, state="free"),
        ),
        {"user_a": items("act_1", "act_2"), "user_d": items("act_7", "act_8")},
    )

    takers = [p.arguments["assignee_id"] for p in invoke(tools).proposed]

    assert takers == ["user_c", "user_c"], "two at most, then nobody left to take"


def test_a_move_never_leaves_the_taker_as_loaded_as_the_giver() -> None:
    tools, _ = tools_for(
        load(person("user_a", "김과부", 3, 2, state="loaded"), person("user_b", "이보통", 1)),
        {"user_a": items("act_1", "act_2")},
    )

    proposals = invoke(tools).proposed

    assert [p.arguments["assignee_id"] for p in proposals] == ["user_b"], "2 -> 2 would be a swap"


def test_the_unowned_row_neither_gives_nor_takes() -> None:
    unowned = person("unowned", "담당 없음", 4)
    tools, calls = tools_for(load(TEAM[0], unowned, TEAM[2]), {"user_a": items("act_1")})

    outcome = invoke(tools)

    assert calls == [{"user_id": "user_a"}]
    assert [p.arguments["assignee_id"] for p in outcome.proposed] == ["user_c"]


def test_an_item_already_flagged_for_reassignment_is_left_alone() -> None:
    tools, _ = tools_for(load(*TEAM), {"user_a": items("act_1", "act_2", flagged=("act_1",))})

    assert [p.arguments["action_item_id"] for p in invoke(tools).proposed] == ["act_2"]


def test_an_unreadable_load_is_a_failed_result_not_a_crash() -> None:
    tools, _ = tools_for(load(ok=False), {})

    outcome = invoke(tools)

    assert outcome.result.ok is False
    assert outcome.proposed == []


def test_it_reads_only_its_allow_list() -> None:
    assert SUBAGENT.tools == (LOAD, ITEMS, BUSY)
    assert not any("speaking" in t or t.startswith("intelligence.") for t in SUBAGENT.tools)
    tools, _ = tools_for(load(*TEAM), {})
    box = Toolbox(tools, SESSION, CallBudget(), allowed=SUBAGENT.tools)
    assert set(box.describe()) == {LOAD, ITEMS}, "no calendar connection registered here"
    assert box.call("intelligence.quality_score").ok is False


def test_the_main_agent_collects_it_and_routes_to_it() -> None:
    assert collect_subagents()["workload"] is SUBAGENT
    tools, _ = tools_for(load(*TEAM), {"user_a": items("act_1")})

    state = run(
        "업무 몰린 사람 있어?",
        session=SESSION,
        router=FakeRouter({"업무": "workload"}),
        subagents={"workload": SUBAGENT},
        tools=tools,
    )

    assert state["route"] == "workload"
    assert [p.tool for p in state["outcome"].proposed] == [REASSIGN]


@pytest.mark.parametrize("n", [1, 4, 9])
def test_never_more_than_five_proposals(n: int) -> None:
    rows = [person(f"user_g{i}", f"과부{i}", 9, 3, state="loaded") for i in range(3)]
    rows += [person(f"user_f{i}", f"여유{i}", 0, state="free") for i in range(n)]
    tools, _ = tools_for(
        load(*rows), {f"user_g{i}": items(f"act_{i}a", f"act_{i}b") for i in range(3)}
    )

    proposals = invoke(tools).proposed

    # B's tool keeps five rows -- three loaded, two free -- so two takers at most.
    assert len(proposals) == 2 * min(n, 2)


def test_the_plan_itself_stops_at_five() -> None:
    people = [plan.Person(f"user_g{i}", "과부", 9, 3, 0, "loaded") for i in range(3)]
    people += [plan.Person(f"user_f{i}", "여유", 0, 0, 0, "free") for i in range(9)]
    items_of = {
        f"user_g{i}": [plan.Candidate(f"act_{i}{j}", "일", 0.5, False) for j in range(3)]
        for i in range(3)
    }

    assert len(plan.plan_moves(people, items_of)) == plan.MAX_MOVES


# --- the team calendar ---------------------------------------------------------------


def test_a_taker_whose_week_is_full_is_passed_over() -> None:
    tools, _ = tools_for(
        load(*TEAM), {"user_a": items("act_1", "act_2")}, busy={"user_c": 0.8, "user_b": 0.1}
    )

    outcome = invoke(tools)

    assert [p.arguments["assignee_id"] for p in outcome.proposed] == ["user_b", "user_b"]
    assert "일정 10%" in outcome.proposed[0].rationale
    assert "팀 캘린더 없이" not in outcome.result.summary


def test_an_unreadable_calendar_is_not_free_and_not_full() -> None:
    tools, _ = tools_for(
        load(*TEAM), {"user_a": items("act_1")}, busy={"user_c": None, "user_b": None}
    )

    (proposal,) = invoke(tools).proposed

    assert proposal.arguments["assignee_id"] == "user_c", "still the one with nothing open"
    assert "캘린더 확인 불가" in proposal.rationale


def test_the_calendar_is_asked_about_takers_only() -> None:
    tools, calls = tools_for(load(*TEAM), {"user_a": items("act_1")}, busy={})

    invoke(tools)

    assert calls[-1] == {"user_ids": ["user_c", "user_b"]}, "not the loaded person"


def test_everyone_booked_proposes_nothing() -> None:
    tools, _ = tools_for(
        load(*TEAM), {"user_a": items("act_1")}, busy={"user_c": 0.9, "user_b": 0.75}
    )

    outcome = invoke(tools)

    assert outcome.proposed == []
    assert "넘겨받을 수 있는 사람이 없어" in outcome.result.summary
