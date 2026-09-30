"""L2 proposals are queued; free text is refused; the newer run supersedes."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_agent.main import Subagent, SubagentState, Toolbox
from autune_agent.main.actions import KEPT_FOR_APPROVAL, Action
from autune_agent.main.pending import ARGUMENT_REFUSED, arguments_ok, queue_l2, scope_for
from autune_agent.main.store import run_and_record
from autune_agent.models import AgentPendingAction, AgentRun
from autune_agent.results import ProposedAction, SubagentResult, ToolResult
from autune_agent.testing import FakeRouter


def _run(session: Session, team: dict[str, str], route: str, meeting: str | None) -> AgentRun:
    row = AgentRun(
        team_id=team["team"],
        meeting_id=meeting,
        trigger={"kind": "chat"},
        outcome="answered",
        route=route,
    )
    session.add(row)
    session.flush()
    return row


def _l2(tool: str, *, kind: str = "k", **arguments: Any) -> ProposedAction:
    return ProposedAction(
        kind=kind,
        title="t",
        tool=tool,
        arguments=arguments,
        level="L2",
        rationale="r",
        evidence=[v for v in arguments.values() if isinstance(v, str) and "_" in v][:1],
    )


@pytest.mark.parametrize(
    ("arguments", "ok"),
    [
        ({"document_id": "rdoc_1"}, True),
        ({"due_date": "2026-10-09", "action_item_id": "act_1"}, True),
        ({"status": "in_progress", "notify": True}, True),
        ({"description": "김 팀장이 금요일까지 배포"}, False),
        ({"status": "x" * 33}, False),
        ({"ids": ["act_1"]}, False),
        ({"김 팀장이 금요일까지 배포": True}, False),
        ({"Status": "in_progress"}, False),
        ({"k" * 33: True}, False),
        ({"due_date": "٢٠٢٦-١٠-٠٩"}, False),
    ],
)
def test_only_ids_and_short_values_pass(arguments: dict[str, Any], ok: bool) -> None:
    assert arguments_ok(arguments) is ok


def test_scope_follows_the_subagent() -> None:
    assert [
        scope_for(s) for s in ("research", "workload", "followup", "report", "briefing", None)
    ] == ["research", "workload", "followup", "report", "any", "any"]


def test_l2_is_queued_and_l1_is_not(session: Session, team: dict[str, str]) -> None:
    run = _run(session, team, "research", team["meeting"])
    l1 = ProposedAction(kind="k", title="t", tool="fake.level_one", level="L1", rationale="r")

    refused = queue_l2(
        session,
        actions={},
        run=run,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_1"), l1],
    )

    rows = session.scalars(select(AgentPendingAction)).all()
    assert refused == []
    assert [(r.tool, r.scope, r.status, r.arguments, r.run_id) for r in rows] == [
        ("agent.share_research_document", "research", "pending", {"document_id": "rdoc_1"}, run.id)
    ]


def test_free_text_is_refused_and_recorded(session: Session, team: dict[str, str]) -> None:
    run = _run(session, team, "workload", None)

    refused = queue_l2(
        session,
        actions={},
        run=run,
        proposed=[_l2("extraction.add_action_item", description="김 팀장 배포")],
    )

    assert session.scalars(select(AgentPendingAction)).all() == []
    assert refused == [
        {
            "tool": "extraction.add_action_item",
            "level": "L2",
            "ok": False,
            "reason": ARGUMENT_REFUSED,
            "evidence": [],
        }
    ]
    assert "김" not in repr(refused)


def test_a_newer_run_supersedes_the_same_subagents_pending_proposal(
    session: Session, team: dict[str, str]
) -> None:
    first = _run(session, team, "research", team["meeting"])
    queue_l2(
        session,
        actions={},
        run=first,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_1")],
    )
    second = _run(session, team, "research", team["meeting"])

    queue_l2(
        session,
        actions={},
        run=second,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_1")],
    )

    statuses = [
        (r.run_id, r.status)
        for r in session.scalars(select(AgentPendingAction).order_by(AgentPendingAction.created_at))
    ]
    assert sorted(s for _, s in statuses) == ["pending", "superseded"]
    assert dict(statuses)[second.id] == "pending"


def test_another_subagent_or_a_decided_row_is_not_superseded(
    session: Session, team: dict[str, str]
) -> None:
    research = _run(session, team, "research", team["meeting"])
    queue_l2(
        session,
        actions={},
        run=research,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_1")],
    )
    decided = session.scalars(select(AgentPendingAction)).one()
    decided.status = "approved"
    report = _run(session, team, "report", team["meeting"])

    queue_l2(
        session,
        actions={},
        run=report,
        proposed=[_l2("intelligence.publish_meeting_report", meeting_id=team["meeting"])],
    )
    research2 = _run(session, team, "research", team["meeting"])
    queue_l2(
        session,
        actions={},
        run=research2,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_2")],
    )

    by_tool = {(r.tool, r.status) for r in session.scalars(select(AgentPendingAction))}
    assert ("agent.share_research_document", "approved") in by_tool
    assert ("intelligence.publish_meeting_report", "pending") in by_tool


def test_a_proposal_about_no_meeting_supersedes_nothing(
    session: Session, team: dict[str, str]
) -> None:
    for _ in range(2):
        run = _run(session, team, "workload", None)
        queue_l2(
            session,
            actions={},
            run=run,
            proposed=[
                _l2("extraction.reassign_action_item", action_item_id="act_1", assignee_id="user_2")
            ],
        )

    assert [r.status for r in session.scalars(select(AgentPendingAction))] == ["pending", "pending"]


def test_two_proposals_of_one_run_both_stay_pending(session: Session, team: dict[str, str]) -> None:
    run = _run(session, team, "research", team["meeting"])

    queue_l2(
        session,
        actions={},
        run=run,
        proposed=[
            _l2("agent.share_research_document", document_id="rdoc_1"),
            _l2("agent.share_research_document", document_id="rdoc_2"),
        ],
    )

    assert [r.status for r in session.scalars(select(AgentPendingAction))] == ["pending", "pending"]


def _noop(**_: Any) -> dict[str, Any]:
    return {"ok": True, "summary": "."}


def test_a_proposal_marked_l1_whose_module_declared_l2_is_queued(
    session: Session, team: dict[str, str]
) -> None:
    """execute_l1 keeps it for approval; queue_l2 must not drop it."""
    run = _run(session, team, "workload", team["meeting"])
    demoted = ProposedAction(
        kind="reassign_action_item",
        title="t",
        tool="fake.post",
        arguments={"action_item_id": "act_1"},
        level="L1",
        rationale="r",
    )
    declared_l1 = ProposedAction(kind="k", title="t", tool="fake.note", level="L1", rationale="r")
    actions = {
        "fake.post": Action("fake.post", _noop, "L2"),
        "fake.note": Action("fake.note", _noop, "L1"),
    }

    refused = queue_l2(session, run=run, proposed=[demoted, declared_l1], actions=actions)

    rows = session.scalars(select(AgentPendingAction)).all()
    assert refused == []
    assert [(r.tool, r.status, r.arguments) for r in rows] == [
        ("fake.post", "pending", {"action_item_id": "act_1"})
    ]


@pytest.mark.parametrize("route", ["Research", "r" * 33])
def test_a_subagent_that_is_not_a_code_name_is_refused(
    session: Session, team: dict[str, str], route: str
) -> None:
    run = _run(session, team, route, team["meeting"])

    refused = queue_l2(
        session,
        run=run,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_1")],
        actions={},
    )

    assert session.scalars(select(AgentPendingAction)).all() == []
    assert [(r["ok"], r["reason"], r["level"]) for r in refused] == [
        (False, ARGUMENT_REFUSED, "L2")
    ]


def test_a_pending_row_whose_run_was_deleted_is_superseded(
    session: Session, team: dict[str, str]
) -> None:
    orphan = AgentPendingAction(
        team_id=team["team"],
        meeting_id=team["meeting"],
        run_id=None,
        subagent="research",
        tool="agent.share_research_document",
        kind="k",
        arguments={"document_id": "rdoc_1"},
        evidence=[],
        scope="research",
    )
    session.add(orphan)
    session.flush()
    run = _run(session, team, "research", team["meeting"])

    queue_l2(
        session,
        run=run,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_2")],
        actions={},
    )

    session.refresh(orphan)
    assert orphan.status == "superseded"


def test_a_run_queues_what_execute_l1_kept_for_approval(
    session: Session, team: dict[str, str]
) -> None:
    """run_and_record hands queue_l2 the same declared actions it gave execute_l1."""
    demoted = ProposedAction(
        kind="reassign_action_item",
        title="t",
        tool="fake.post",
        arguments={"action_item_id": "act_1"},
        level="L1",
        rationale="r",
    )

    def build(toolbox: Toolbox) -> Any:
        def act(state: SubagentState) -> SubagentState:
            result = ToolResult(ok=True, summary="제안합니다.")
            return {"outcome": SubagentResult(result=result, proposed=[demoted])}

        graph = StateGraph(SubagentState)
        graph.add_node("act", act)
        graph.add_edge(START, "act")
        graph.add_edge("act", END)
        return graph.compile()

    subagent = Subagent(name="workload", description="Use this in tests.", tools=(), build=build)
    calls: list[Any] = []

    def post(**arguments: Any) -> dict[str, Any]:
        calls.append(arguments)
        return {"ok": True, "summary": "."}

    row, _ = run_and_record(
        "업무",
        session=session,
        router=FakeRouter({"업무": "workload"}),
        team_id=team["team"],
        meeting_id=team["meeting"],
        trigger={"kind": "chat"},
        subagents={"workload": subagent},
        tools={},
        actions={"fake.post": Action("fake.post", post, "L2")},
    )

    assert calls == []  # never run at L1
    assert [a["reason"] for a in row.actions] == [KEPT_FOR_APPROVAL]
    queued = session.scalars(select(AgentPendingAction)).all()
    assert [(q.tool, q.status, q.run_id) for q in queued] == [("fake.post", "pending", row.id)]
