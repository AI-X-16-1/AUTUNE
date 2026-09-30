"""An event wakes the subagents that asked for it (agent-layer.md section 6)."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_agent import tasks
from autune_agent.config import AgentSettings
from autune_agent.main import TRIGGER_EVENTS, Subagent, SubagentState, Toolbox, on_event
from autune_agent.main import triggers as triggers_module
from autune_agent.models import AgentRun
from autune_agent.results import SubagentResult, ToolResult
from autune_contracts import (
    CONTRACT_VERSION,
    EXTRACTION_COMPLETED,
    INTELLIGENCE_COMPLETED,
    TRANSCRIPT_READY,
)
from autune_core import consumer_task_suffix


def _woken(
    name: str, triggers: tuple[str, ...], seen: list[str], *, fail: bool = False
) -> Subagent:
    def build(toolbox: Toolbox) -> Any:
        def act(state: SubagentState) -> SubagentState:
            seen.append(state["request"])
            if fail:
                raise RuntimeError("bug")
            return {"outcome": SubagentResult(result=ToolResult(ok=True, summary=f"{name} 요약."))}

        graph = StateGraph(SubagentState)
        graph.add_node("act", act)
        graph.add_edge(START, "act")
        graph.add_edge("act", END)
        return graph.compile()

    return Subagent(
        name=name, description="Use this in tests.", tools=(), build=build, triggers=triggers
    )


def test_only_the_subagents_that_asked_are_woken(session: Session, team: dict[str, str]) -> None:
    seen: list[str] = []
    subagents = {
        "report": _woken("report", (INTELLIGENCE_COMPLETED,), seen),
        "research": _woken("research", (TRANSCRIPT_READY,), seen),
    }

    rows = on_event(
        INTELLIGENCE_COMPLETED, team["meeting"], session=session, subagents=subagents, tools={}
    )

    assert [r.route for r in rows] == ["report"]
    row = rows[0]
    assert row.meeting_id == team["meeting"]
    assert row.team_id == team["team"]
    assert row.trigger == {"kind": "event", "event": INTELLIGENCE_COMPLETED}
    assert row.answer is None  # no run keeps its answer (main/store.py)
    assert seen == [INTELLIGENCE_COMPLETED]


def test_a_redelivered_event_does_not_run_twice(session: Session, team: dict[str, str]) -> None:
    seen: list[str] = []
    subagents = {"research": _woken("research", (TRANSCRIPT_READY,), seen)}

    on_event(TRANSCRIPT_READY, team["meeting"], session=session, subagents=subagents, tools={})
    again = on_event(
        TRANSCRIPT_READY, team["meeting"], session=session, subagents=subagents, tools={}
    )

    assert again == []
    assert len(seen) == 1


def test_a_failed_run_is_retried_and_does_not_stop_the_others(
    session: Session, team: dict[str, str]
) -> None:
    seen: list[str] = []
    subagents = {
        "research": _woken("research", (TRANSCRIPT_READY,), seen, fail=True),
        "followup": _woken("followup", (TRANSCRIPT_READY,), seen),
    }

    first = on_event(
        TRANSCRIPT_READY, team["meeting"], session=session, subagents=subagents, tools={}
    )
    second = on_event(
        TRANSCRIPT_READY, team["meeting"], session=session, subagents=subagents, tools={}
    )

    assert [r.route for r in first] == ["followup"]
    assert second == []  # research failed again; followup is done
    outcomes = sorted(session.scalars(select(AgentRun.outcome).where(AgentRun.route == "research")))
    assert outcomes == ["failed", "failed"]


def test_a_meeting_deleted_before_the_event_arrived_is_nothing_to_do(session: Session) -> None:
    subagents = {"research": _woken("research", (TRANSCRIPT_READY,), [])}

    assert on_event(TRANSCRIPT_READY, "mtg_gone", session=session, subagents=subagents) == []


def test_the_layer_switched_off_wakes_nobody(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        triggers_module, "get_agent_settings", lambda: AgentSettings(router_impl="off")
    )
    seen: list[str] = []
    subagents = {"research": _woken("research", (TRANSCRIPT_READY,), seen)}

    assert on_event(TRANSCRIPT_READY, team["meeting"], session=session, subagents=subagents) == []
    assert seen == []


def test_a_subagent_may_only_ask_for_an_event_the_layer_listens_to() -> None:
    with pytest.raises(ValueError):
        _woken("research", (EXTRACTION_COMPLETED,), [])


def test_every_trigger_event_has_its_task() -> None:
    for event in TRIGGER_EVENTS:
        name = f"autune.agent.{consumer_task_suffix(event)}"
        assert getattr(tasks, consumer_task_suffix(event)).name == name


def test_the_payload_is_read_for_its_meeting_id_only() -> None:
    payload = {"contract_version": CONTRACT_VERSION, "meeting_id": "mtg_abc", "utterances": ["..."]}

    assert tasks._meeting_id(payload) == "mtg_abc"


def test_a_payload_from_another_major_version_is_refused() -> None:
    with pytest.raises(ValueError):
        tasks._meeting_id({"contract_version": "99.0", "meeting_id": "mtg_abc"})


def test_a_woken_run_answers_with_the_summary_and_routes_nothing() -> None:
    router = triggers_module.SummaryRouter()
    outcome = SubagentResult(result=ToolResult(ok=True, summary="요약."))

    assert router.route("anything", {"report": "Use this."}) is None
    assert router.compose("anything", outcome) == "요약."


def test_a_privacy_violation_fails_the_task_after_the_others_ran(
    session: Session, team: dict[str, str]
) -> None:
    """Other exceptions are logged; this one is raised, names only (#509 review)."""
    from autune_core.errors import PrivacyViolationError

    seen: list[str] = []

    def leaking(toolbox: Toolbox) -> Any:
        def act(state: SubagentState) -> SubagentState:
            raise PrivacyViolationError("refused 010-1234-5678")

        graph = StateGraph(SubagentState)
        graph.add_node("act", act)
        graph.add_edge(START, "act")
        graph.add_edge("act", END)
        return graph.compile()

    subagents = {
        "research": Subagent(
            name="research",
            description="Use this in tests.",
            tools=(),
            build=leaking,
            triggers=(INTELLIGENCE_COMPLETED,),
        ),
        "report": _woken("report", (INTELLIGENCE_COMPLETED,), seen),
    }

    with pytest.raises(PrivacyViolationError) as caught:
        on_event(
            INTELLIGENCE_COMPLETED,
            team["meeting"],
            session=session,
            subagents=subagents,
            tools={},
        )

    assert seen == [INTELLIGENCE_COMPLETED]  # report still ran
    assert "research" in str(caught.value)
    assert "010" not in str(caught.value)
