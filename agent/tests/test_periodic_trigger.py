"""A timer wakes the subagents that declared a period (#634, agent-layer.md 3.1)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_agent import tasks
from autune_agent.config import AgentSettings
from autune_agent.main import (
    PERIODIC_REQUEST,
    Periodic,
    Subagent,
    SubagentState,
    Toolbox,
    on_tick,
)
from autune_agent.main import triggers as triggers_module
from autune_agent.main.subagents import PERIODIC_TICK
from autune_agent.models import AgentPendingAction, AgentRun
from autune_agent.results import ProposedAction, SubagentResult, ToolResult
from autune_contracts import INTELLIGENCE_COMPLETED
from autune_core import Team
from autune_core.periodic import is_periodic_task_name, schedule_of


def _subagent(
    name: str,
    triggers: tuple[str | Periodic, ...],
    seen: list[str],
    *,
    fail: bool = False,
    proposes: ProposedAction | None = None,
    proposals_per: Any = "meeting",
) -> Subagent:
    def build(toolbox: Toolbox) -> Any:
        def act(state: SubagentState) -> SubagentState:
            seen.append(state["request"])
            if fail:
                raise RuntimeError("bug")
            result = ToolResult(ok=True, summary=f"{name} 요약.")
            proposed = [proposes] if proposes is not None else []
            return {"outcome": SubagentResult(result=result, proposed=proposed)}

        graph = StateGraph(SubagentState)
        graph.add_node("act", act)
        graph.add_edge(START, "act")
        graph.add_edge("act", END)
        return graph.compile()

    return Subagent(
        name=name,
        description="Use this in tests.",
        tools=(),
        build=build,
        triggers=triggers,
        proposals_per=proposals_per,
    )


def _first_run_time(session: Session) -> Any:
    return session.scalars(select(AgentRun.created_at).order_by(AgentRun.created_at)).first()


def test_only_subagents_with_a_period_are_woken_and_the_run_is_about_the_team(
    session: Session, team: dict[str, str]
) -> None:
    seen: list[str] = []
    subagents = {
        "workload": _subagent("workload", (Periodic(hours=6),), seen),
        "report": _subagent("report", (INTELLIGENCE_COMPLETED,), seen),
    }

    rows = on_tick(session=session, subagents=subagents, tools={}, task_id="t1")

    assert [(r.route, r.team_id, r.meeting_id) for r in rows] == [("workload", team["team"], None)]
    assert rows[0].trigger == {"kind": "periodic", "task_id": "t1"}
    assert seen == [PERIODIC_REQUEST]


def test_a_team_with_no_members_is_skipped(session: Session, team: dict[str, str]) -> None:
    session.add(Team(name="빈 팀"))
    session.commit()
    seen: list[str] = []
    subagents = {"workload": _subagent("workload", (Periodic(hours=6),), seen)}

    rows = on_tick(session=session, subagents=subagents, tools={})

    assert [r.team_id for r in rows] == [team["team"]]


def test_a_redelivered_or_early_tick_does_not_run_again_and_a_due_one_does(
    session: Session, team: dict[str, str]
) -> None:
    seen: list[str] = []
    subagents = {"workload": _subagent("workload", (Periodic(hours=6),), seen)}
    on_tick(session=session, subagents=subagents, tools={}, task_id="t1")
    started = _first_run_time(session)

    again = on_tick(session=session, subagents=subagents, tools={}, now=started, task_id="t1")
    next_tick = on_tick(session=session, subagents=subagents, tools={}, now=started + PERIODIC_TICK)
    # Six ticks later, a few seconds early: half a tick of slack, so no drift to seven.
    due = on_tick(
        session=session,
        subagents=subagents,
        tools={},
        now=started + 6 * PERIODIC_TICK - timedelta(seconds=5),
    )

    assert (again, next_tick) == ([], [])
    assert [r.route for r in due] == ["workload"]
    assert len(seen) == 2


def test_a_failed_run_is_retried_on_the_next_tick(session: Session, team: dict[str, str]) -> None:
    seen: list[str] = []
    broken = {"workload": _subagent("workload", (Periodic(hours=6),), seen, fail=True)}
    on_tick(session=session, subagents=broken, tools={})
    started = _first_run_time(session)
    fixed = {"workload": _subagent("workload", (Periodic(hours=6),), seen)}

    rows = on_tick(session=session, subagents=fixed, tools={}, now=started + PERIODIC_TICK)

    assert [r.outcome for r in rows] == ["answered"]


def test_the_layer_switched_off_wakes_nobody(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        triggers_module, "get_agent_settings", lambda: AgentSettings(router_impl="off")
    )
    seen: list[str] = []
    subagents = {"workload": _subagent("workload", (Periodic(hours=6),), seen)}

    assert on_tick(session=session, subagents=subagents) == []
    assert seen == []


def test_a_team_wide_periodic_proposal_replaces_the_last_one(
    session: Session, team: dict[str, str]
) -> None:
    """With #631, each periodic Workload run leaves one pending proposal, not one more."""
    reassign = ProposedAction(
        kind="reassign_action_item",
        title="t",
        tool="extraction.reassign_action_item",
        arguments={"action_item_id": "act_1", "assignee_id": "user_2"},
        level="L2",
        rationale="r",
    )
    seen: list[str] = []
    subagents = {
        "workload": _subagent(
            "workload", (Periodic(hours=6),), seen, proposes=reassign, proposals_per="team"
        )
    }
    on_tick(session=session, subagents=subagents, tools={}, actions={})
    started = _first_run_time(session)

    on_tick(
        session=session, subagents=subagents, tools={}, actions={}, now=started + timedelta(hours=7)
    )

    assert sorted(r.status for r in session.scalars(select(AgentPendingAction))) == [
        "pending",
        "superseded",
    ]


@pytest.mark.parametrize(
    "triggers", [(Periodic(hours=6), Periodic(hours=12)), ("autune.extraction.completed",)]
)
def test_a_subagent_declares_one_period_and_known_events(triggers: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError):
        _subagent("workload", triggers, [])


def test_a_period_shorter_than_the_tick_is_refused() -> None:
    with pytest.raises(ValueError):
        Periodic(hours=0)


def test_the_tick_is_on_beats_schedule() -> None:
    assert is_periodic_task_name(tasks.wake_subagents.name)
    assert schedule_of(tasks.wake_subagents) == PERIODIC_TICK
