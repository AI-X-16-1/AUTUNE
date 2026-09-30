"""L2 proposals are queued; free text is refused; the newer run supersedes."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_agent.main.pending import ARGUMENT_REFUSED, arguments_ok, queue_l2, scope_for
from autune_agent.models import AgentPendingAction, AgentRun
from autune_agent.results import ProposedAction


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


def _l2(tool: str, **arguments: Any) -> ProposedAction:
    return ProposedAction(
        kind="k",
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
    l1 = ProposedAction(kind="k", title="t", tool="fake.l1", level="L1", rationale="r")

    refused = queue_l2(
        session, run=run, proposed=[_l2("agent.share_research_document", document_id="rdoc_1"), l1]
    )

    rows = session.scalars(select(AgentPendingAction)).all()
    assert refused == []
    assert [(r.tool, r.scope, r.status, r.arguments, r.run_id) for r in rows] == [
        ("agent.share_research_document", "research", "pending", {"document_id": "rdoc_1"}, run.id)
    ]


def test_free_text_is_refused_and_recorded(session: Session, team: dict[str, str]) -> None:
    run = _run(session, team, "workload", None)

    refused = queue_l2(
        session, run=run, proposed=[_l2("extraction.add_action_item", description="김 팀장 배포")]
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
        session, run=first, proposed=[_l2("agent.share_research_document", document_id="rdoc_1")]
    )
    second = _run(session, team, "research", team["meeting"])

    queue_l2(
        session, run=second, proposed=[_l2("agent.share_research_document", document_id="rdoc_1")]
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
        session, run=research, proposed=[_l2("agent.share_research_document", document_id="rdoc_1")]
    )
    decided = session.scalars(select(AgentPendingAction)).one()
    decided.status = "approved"
    report = _run(session, team, "report", team["meeting"])

    queue_l2(
        session,
        run=report,
        proposed=[_l2("intelligence.publish_meeting_report", meeting_id=team["meeting"])],
    )
    research2 = _run(session, team, "research", team["meeting"])
    queue_l2(
        session,
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
        run=run,
        proposed=[
            _l2("agent.share_research_document", document_id="rdoc_1"),
            _l2("agent.share_research_document", document_id="rdoc_2"),
        ],
    )

    assert [r.status for r in session.scalars(select(AgentPendingAction))] == ["pending", "pending"]
