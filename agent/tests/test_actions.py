"""L1 runs at the end of the run; L2 waits (agent-layer.md section 8)."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_agent.main import (
    Action,
    RunScope,
    Subagent,
    SubagentState,
    Toolbox,
    ToolContractError,
    collect_actions,
    execute_l1,
)
from autune_agent.main.store import run_and_record
from autune_agent.models import AgentPendingAction, AgentRun
from autune_agent.results import ProposedAction, SubagentResult, ToolResult
from autune_agent.testing import FakeRouter
from autune_core import Meeting, Team

SESSION: Any = object()
SCOPE = RunScope(team_id="team_a")

DONE = {"ok": True, "summary": "했습니다.", "evidence": ["act_1"]}


def _proposal(tool: str, level: str = "L1", **arguments: Any) -> ProposedAction:
    return ProposedAction(
        kind="note", title="t", tool=tool, level=level, rationale="r", arguments=arguments
    )


def _recorder(calls: list[dict[str, Any]], result: dict[str, Any] = DONE) -> Any:
    def draft_note(team_id: str, body: str) -> dict[str, Any]:
        calls.append({"team_id": team_id, "body": body})
        return result

    return draft_note


def test_the_module_decides_the_level(monkeypatch: pytest.MonkeyPatch) -> None:
    def draft(team_id: str) -> dict[str, Any]:
        return DONE

    def post(team_id: str) -> dict[str, Any]:
        return DONE

    _fake_module(monkeypatch, ACTIONS=[draft, post], L1_ACTIONS=[draft])

    actions = collect_actions(["fake"])

    assert {n: a.level for n, a in actions.items()} == {"fake.draft": "L1", "fake.post": "L2"}


def test_an_l1_action_outside_actions_is_a_mistake(monkeypatch: pytest.MonkeyPatch) -> None:
    def draft(team_id: str) -> dict[str, Any]:
        return DONE

    _fake_module(monkeypatch, ACTIONS=[], L1_ACTIONS=[draft])

    with pytest.raises(ToolContractError):
        collect_actions(["fake"])


def test_l1_runs_with_the_runs_team_and_keeps_no_arguments() -> None:
    calls: list[dict[str, Any]] = []
    actions = {"fake.draft_note": Action("fake.draft_note", _recorder(calls), "L1")}

    done = execute_l1(
        [_proposal("fake.draft_note", body="회의 내용 초안")],
        actions=actions,
        session=SESSION,
        scope=SCOPE,
    )

    assert calls == [{"team_id": "team_a", "body": "회의 내용 초안"}]
    assert done == [
        {
            "tool": "fake.draft_note",
            "level": "L1",
            "ok": True,
            "reason": None,
            "evidence": ["act_1"],
        }
    ]


def test_a_proposal_cannot_demote_a_write_its_module_calls_l2() -> None:
    calls: list[dict[str, Any]] = []
    actions = {"fake.post": Action("fake.post", _recorder(calls), "L2")}

    done = execute_l1(
        [_proposal("fake.post", body="x")], actions=actions, session=SESSION, scope=SCOPE
    )

    assert calls == []
    assert done[0]["ok"] is False
    assert done[0]["reason"] == "its module declares it L2; it waits for approval"


def test_l2_is_left_for_plan_mode() -> None:
    calls: list[dict[str, Any]] = []
    actions = {"fake.draft_note": Action("fake.draft_note", _recorder(calls), "L1")}

    done = execute_l1(
        [_proposal("fake.draft_note", level="L2", body="x")],
        actions=actions,
        session=SESSION,
        scope=SCOPE,
    )

    assert calls == []
    assert done == []


def test_another_teams_id_from_the_model_is_refused() -> None:
    calls: list[dict[str, Any]] = []
    actions = {"fake.draft_note": Action("fake.draft_note", _recorder(calls), "L1")}

    done = execute_l1(
        [_proposal("fake.draft_note", team_id="team_b", body="x")],
        actions=actions,
        session=SESSION,
        scope=SCOPE,
    )

    assert calls == []
    assert done[0]["reason"] == "team_id is outside this run's team"


def test_one_broken_action_does_not_stop_the_next() -> None:
    def broken(team_id: str) -> dict[str, Any]:
        raise RuntimeError("김 팀장 010-1234-5678")

    calls: list[dict[str, Any]] = []
    actions = {
        "fake.broken": Action("fake.broken", broken, "L1"),
        "fake.draft_note": Action("fake.draft_note", _recorder(calls), "L1"),
    }

    done = execute_l1(
        [
            _proposal("fake.broken"),
            _proposal("fake.nobody"),
            _proposal("fake.draft_note", body="x"),
        ],
        actions=actions,
        session=SESSION,
        scope=SCOPE,
    )

    assert [(d["tool"], d["ok"], d["reason"]) for d in done] == [
        ("fake.broken", False, "the action failed"),
        ("fake.nobody", False, "not a declared action"),
        ("fake.draft_note", True, None),
    ]


def test_a_run_records_what_ran_and_leaves_l2_proposed(
    session: Session, team: dict[str, str]
) -> None:
    calls: list[dict[str, Any]] = []
    actions = {
        "fake.draft_note": Action("fake.draft_note", _recorder(calls), "L1"),
        "fake.post": Action("fake.post", _recorder(calls), "L2"),
    }
    proposals = [
        _proposal("fake.draft_note", body="초안"),
        _proposal("fake.post", "L2", post_id="note_1"),
    ]

    row, state = run_and_record(
        "보고서",
        session=session,
        router=FakeRouter({"보고서": "report"}),
        team_id=team["team"],
        meeting_id=team["meeting"],
        trigger={"kind": "chat"},
        subagents={"report": _proposing("report", proposals)},
        tools={},
        actions=actions,
    )

    assert calls == [{"team_id": team["team"], "body": "초안"}]
    assert [a["tool"] for a in row.actions] == ["fake.draft_note"]
    assert [p["tool"] for p in row.proposed] == ["fake.draft_note", "fake.post"]
    assert "초안" not in str(row.actions)
    assert [p.tool for p in session.scalars(select(AgentPendingAction))] == ["fake.post"]


def test_a_meeting_of_another_team_is_not_reached_by_an_action(
    session: Session, team: dict[str, str]
) -> None:
    other = Team(name="다른 팀")
    session.add(other)
    session.flush()
    theirs = Meeting(team_id=other.id, title="남의 회의")
    session.add(theirs)
    session.commit()

    def attach(team_id: str, meeting_id: str) -> dict[str, Any]:
        raise AssertionError("must not run")

    done = execute_l1(
        [_proposal("fake.attach", meeting_id=theirs.id)],
        actions={"fake.attach": Action("fake.attach", attach, "L1")},
        session=session,
        scope=RunScope(team_id=team["team"], meeting_id=team["meeting"]),
    )

    assert done[0]["reason"] == "meeting not found"


def _proposing(name: str, proposals: list[ProposedAction]) -> Subagent:
    def build(toolbox: Toolbox) -> Any:
        def act(state: SubagentState) -> SubagentState:
            result = ToolResult(ok=True, summary="제안했습니다.")
            return {"outcome": SubagentResult(result=result, proposed=proposals)}

        graph = StateGraph(SubagentState)
        graph.add_node("act", act)
        graph.add_edge(START, "act")
        graph.add_edge("act", END)
        return graph.compile()

    return Subagent(name=name, description="Use this in tests.", tools=(), build=build)


def _fake_module(monkeypatch: pytest.MonkeyPatch, **attrs: Any) -> None:
    package = types.ModuleType("autune_fake")
    module = types.ModuleType("autune_fake.tools")
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, "autune_fake", package)
    monkeypatch.setitem(sys.modules, "autune_fake.tools", module)


def test_a_modules_own_refusal_reason_is_not_kept() -> None:
    """It may repeat what the model wrote; only this layer's reasons are stored."""

    def set_due(team_id: str, due_date: str) -> dict[str, Any]:
        return {"ok": False, "reason": f"not a date: {due_date!r}", "summary": "날짜가 아닙니다."}

    done = execute_l1(
        [_proposal("fake.set_due", due_date="김 팀장 생일")],
        actions={"fake.set_due": Action("fake.set_due", set_due, "L1")},
        session=SESSION,
        scope=SCOPE,
    )

    assert done[0]["ok"] is False
    assert done[0]["reason"] is None


def test_a_proposals_evidence_is_ids_only() -> None:
    with pytest.raises(ValueError):
        ProposedAction(
            kind="note",
            title="t",
            tool="x.y",
            level="L1",
            rationale="r",
            evidence=["김 팀장이 말함"],
        )


def test_an_action_missing_its_meeting_in_a_chat_run_is_refused_and_the_reason_kept() -> None:
    def attach(team_id: str, meeting_id: str) -> dict[str, Any]:
        raise AssertionError("must not run")

    done = execute_l1(
        [_proposal("fake.attach")],
        actions={"fake.attach": Action("fake.attach", attach, "L1")},
        session=SESSION,
        scope=SCOPE,
    )

    assert done[0]["reason"] == "this run is about no meeting; pass meeting_id"


def test_a_privacy_violation_is_raised_after_the_other_actions_ran() -> None:
    """Never downgraded to a log line (#509 review, the shape #506 uses)."""
    from autune_agent.main.actions import ActionPrivacyViolationError
    from autune_core.errors import PrivacyViolationError

    def leaks(team_id: str) -> dict[str, Any]:
        raise PrivacyViolationError("refused 010-1234-5678")

    calls: list[dict[str, Any]] = []
    actions = {
        "fake.leaks": Action("fake.leaks", leaks, "L1"),
        "fake.draft_note": Action("fake.draft_note", _recorder(calls), "L1"),
    }

    with pytest.raises(ActionPrivacyViolationError) as caught:
        execute_l1(
            [_proposal("fake.leaks"), _proposal("fake.draft_note", body="x")],
            actions=actions,
            session=SESSION,
            scope=SCOPE,
        )

    assert calls == [{"team_id": "team_a", "body": "x"}]  # the next action still ran
    assert [(d["tool"], d["ok"]) for d in caught.value.done] == [
        ("fake.leaks", False),
        ("fake.draft_note", True),
    ]
    assert "010" not in str(caught.value)
    assert "fake.leaks" in str(caught.value)


def test_a_run_whose_action_leaks_is_failed_and_keeps_what_ran(
    session: Session, team: dict[str, str]
) -> None:
    from autune_core.errors import PrivacyViolationError

    def leaks(team_id: str) -> dict[str, Any]:
        raise PrivacyViolationError("refused")

    calls: list[dict[str, Any]] = []
    actions = {
        "fake.leaks": Action("fake.leaks", leaks, "L1"),
        "fake.draft_note": Action("fake.draft_note", _recorder(calls), "L1"),
    }
    proposals = [_proposal("fake.leaks"), _proposal("fake.draft_note", body="초안")]

    with pytest.raises(PrivacyViolationError):
        run_and_record(
            "보고서",
            session=session,
            router=FakeRouter({"보고서": "report"}),
            team_id=team["team"],
            meeting_id=team["meeting"],
            trigger={"kind": "chat"},
            subagents={"report": _proposing("report", proposals)},
            tools={},
            actions=actions,
        )

    row = session.scalars(select(AgentRun)).one()
    assert row.outcome == "failed"
    assert row.route == "report"
    assert [(a["tool"], a["ok"]) for a in row.actions] == [
        ("fake.leaks", False),
        ("fake.draft_note", True),
    ]
