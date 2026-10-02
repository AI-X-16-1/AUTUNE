"""A run that leaves pending proposals tells the approvers, by count and link (#632)."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from autune_agent.main import Subagent, SubagentState, Toolbox
from autune_agent.main import notify as notify_module
from autune_agent.main.notify import APPROVALS_PATH, notify_approvers, tell_approvers
from autune_agent.main.store import run_and_record
from autune_agent.main.triggers import on_event
from autune_agent.models import AgentApprover, AgentPendingAction, AgentRun
from autune_agent.results import ProposedAction, SubagentResult, ToolResult
from autune_agent.testing import FakeRouter
from autune_contracts import INTELLIGENCE_COMPLETED
from autune_core import TeamMember, User, get_settings
from autune_core.errors import PrivacyViolationError
from autune_integrations.errors import SlackRecipientNotLinkedError
from autune_integrations.fakes import FakeSlack


def _person(session: Session, team: dict[str, str], email: str, *scopes: str) -> str:
    user = User(email=email, display_name=email.split("@")[0])
    session.add(user)
    session.flush()
    session.add(TeamMember(team_id=team["team"], user_id=user.id))
    for scope in scopes:
        session.add(AgentApprover(team_id=team["team"], user_id=user.id, scope=scope))
    session.flush()
    return user.id


def _run(
    session: Session, team: dict[str, str], route: str, *scopes: str, by: str | None = None
) -> AgentRun:
    row = AgentRun(
        team_id=team["team"],
        meeting_id=team["meeting"],
        requested_by=by,
        trigger={"kind": "event"},
        outcome="answered",
        route=route,
    )
    session.add(row)
    session.flush()
    for scope in scopes:
        session.add(
            AgentPendingAction(
                team_id=team["team"],
                meeting_id=team["meeting"],
                run_id=row.id,
                subagent=route,
                tool="t.x",
                kind="k",
                arguments={},
                evidence=[],
                scope=scope,
            )
        )
    session.flush()
    return row


def _about(run: AgentRun) -> dict[str, Any]:
    return {"team_id": run.team_id, "run_ids": [run.id], "asked_by": run.requested_by}


def test_the_approvers_of_the_scope_and_of_any_get_one_dm_each(
    session: Session, team: dict[str, str]
) -> None:
    lead = _person(session, team, "lead@example.com", "research")
    manager = _person(session, team, "manager@example.com", "any")
    _person(session, team, "other@example.com", "report")
    _person(session, team, "plain@example.com")
    run = _run(session, team, "research", "research", "research")
    slack = FakeSlack()

    sent = notify_approvers(session, **_about(run), slack=slack)

    assert sent == sorted([lead, manager])
    assert [m.channel for m in slack.sent] == sorted([lead, manager])
    link = get_settings().web_base_url.rstrip("/") + APPROVALS_PATH
    assert all(m.is_dm and m.text.endswith(link) and "2건" in m.text for m in slack.sent)


def test_the_message_holds_a_count_and_a_link_only(session: Session, team: dict[str, str]) -> None:
    _person(session, team, "lead@example.com", "research")
    run = _run(session, team, "research", "research")
    slack = FakeSlack()

    notify_approvers(session, **_about(run), slack=slack)

    (message,) = slack.sent
    assert message.text == (
        f"승인을 기다리는 에이전트 제안이 1건 있습니다. "
        f"{get_settings().web_base_url.rstrip('/')}{APPROVALS_PATH}"
    )
    assert "주간 회의" not in message.text  # the meeting's title stays behind sign-in


def test_the_count_is_everything_waiting_for_that_approver(
    session: Session, team: dict[str, str]
) -> None:
    lead = _person(session, team, "lead@example.com", "research")
    _run(session, team, "research", "research")
    _run(session, team, "report", "report")
    run = _run(session, team, "research", "research")
    slack = FakeSlack()

    notify_approvers(session, **_about(run), slack=slack)

    assert [(m.channel, "2건" in m.text) for m in slack.sent] == [(lead, True)]


def test_the_person_who_asked_is_not_told_about_their_own_run(
    session: Session, team: dict[str, str]
) -> None:
    asker = _person(session, team, "lead@example.com", "research")
    run = _run(session, team, "research", "research", by=asker)
    slack = FakeSlack()

    assert notify_approvers(session, **_about(run), slack=slack) == []
    assert slack.sent == []


def test_an_approver_who_left_the_team_is_not_told(session: Session, team: dict[str, str]) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["outsider"], scope="any"))
    run = _run(session, team, "research", "research")
    slack = FakeSlack()

    assert notify_approvers(session, **_about(run), slack=slack) == []


def test_a_run_with_nothing_pending_tells_nobody(session: Session, team: dict[str, str]) -> None:
    _person(session, team, "lead@example.com", "any")
    run = _run(session, team, "research")
    slack = FakeSlack()

    assert notify_approvers(session, **_about(run), slack=slack) == []


def test_one_approvers_slack_failing_does_not_stop_the_others(
    session: Session, team: dict[str, str]
) -> None:
    broken = _person(session, team, "a@example.com", "any")
    fine = _person(session, team, "b@example.com", "any")
    run = _run(session, team, "research", "research")

    class Flaky(FakeSlack):
        def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = None) -> str:
            if user_id == broken:
                raise RuntimeError("not linked")
            return super().send_dm(user_id, text, blocks)

    assert notify_approvers(session, **_about(run), slack=Flaky()) == [fine]


def test_a_team_without_slack_is_told_nothing(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    looked_up: list[tuple[str, str]] = []

    def no_integration(session: Session, team_id: str, service: str) -> None:
        looked_up.append((team_id, service))

    monkeypatch.setattr(notify_module, "load_integration", no_integration)
    _person(session, team, "lead@example.com", "any")
    run = _run(session, team, "research", "research")

    assert notify_approvers(session, **_about(run)) == []
    assert looked_up == [(team["team"], "slack")]


def _proposing_subagent() -> Subagent:
    proposal = ProposedAction(
        kind="share",
        title="t",
        tool="agent.share_research_document",
        arguments={"document_id": "rdoc_1"},
        level="L2",
        rationale="r",
    )

    def build(toolbox: Toolbox) -> Any:
        def act(state: SubagentState) -> SubagentState:
            result = ToolResult(ok=True, summary="제안합니다.")
            return {"outcome": SubagentResult(result=result, proposed=[proposal])}

        graph = StateGraph(SubagentState)
        graph.add_node("act", act)
        graph.add_edge(START, "act")
        graph.add_edge("act", END)
        return graph.compile()

    return Subagent(name="research", description="Use this in tests.", tools=(), build=build)


def _record(session: Session, team: dict[str, str]) -> AgentRun:
    row, _ = run_and_record(
        "intelligence.completed",
        session=session,
        router=FakeRouter({}),
        team_id=team["team"],
        meeting_id=team["meeting"],
        trigger={"kind": "event"},
        subagents={"research": _proposing_subagent()},
        tools={},
        actions={},
        route_to="research",
    )
    return row


def test_a_recorded_run_that_queued_tells_the_approvers(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    told: list[str] = []
    monkeypatch.setattr(
        notify_module,
        "notify_approvers",
        lambda session, *, team_id, run_ids, asked_by: told.extend(run_ids) or [],
    )

    row = _record(session, team)

    assert told == [row.id]


def test_a_failing_notification_does_not_fail_the_run(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(session: Session, **_: Any) -> list[str]:
        raise RuntimeError("slack is down")

    monkeypatch.setattr(notify_module, "notify_approvers", boom)

    row = _record(session, team)

    assert row.outcome == "answered"
    assert session.query(AgentPendingAction).filter_by(run_id=row.id).count() == 1


def test_an_unlinked_approver_is_skipped_and_a_privacy_violation_is_raised(
    session: Session, team: dict[str, str]
) -> None:
    unlinked = _person(session, team, "a@example.com", "any")
    _person(session, team, "b@example.com", "any")
    run = _run(session, team, "research", "research")

    class Strict(FakeSlack):
        def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = None) -> str:
            if user_id == unlinked:
                raise SlackRecipientNotLinkedError("not linked")
            raise PrivacyViolationError("unmasked")

    with pytest.raises(PrivacyViolationError):
        notify_approvers(session, **_about(run), slack=Strict())


def test_a_failing_notification_is_rolled_back_for_the_caller(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trigger keeps using the session; an aborted transaction would stop its next run."""
    rolled_back: list[bool] = []
    monkeypatch.setattr(session, "rollback", lambda: rolled_back.append(True))

    def boom(session: Session, **_: Any) -> list[str]:
        raise RuntimeError("database went away")

    monkeypatch.setattr(notify_module, "notify_approvers", boom)

    tell_approvers(session, team_id=team["team"], run_ids=["run_1"])

    assert rolled_back == [True]


def _woken(name: str) -> Subagent:
    proposal = ProposedAction(
        kind="k",
        title="t",
        tool=f"agent.{name}_thing",
        arguments={"document_id": "rdoc_1"},
        level="L2",
        rationale="r",
    )

    def build(toolbox: Toolbox) -> Any:
        def act(state: SubagentState) -> SubagentState:
            result = ToolResult(ok=True, summary="제안합니다.")
            return {"outcome": SubagentResult(result=result, proposed=[proposal])}

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
        triggers=(INTELLIGENCE_COMPLETED,),
    )


def test_one_event_that_wakes_three_subagents_tells_the_team_once(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#638 review: Follow-up, Research and Report all wake on intelligence.completed."""
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        notify_module,
        "notify_approvers",
        lambda session, *, team_id, run_ids, asked_by: calls.append((team_id, list(run_ids))) or [],
    )
    subagents = {name: _woken(name) for name in ("followup", "research", "report")}

    rows = on_event(
        INTELLIGENCE_COMPLETED,
        team["meeting"],
        session=session,
        subagents=subagents,
        tools={},
        actions={},
    )

    assert len(rows) == 3
    assert calls == [(team["team"], [r.id for r in rows])]
