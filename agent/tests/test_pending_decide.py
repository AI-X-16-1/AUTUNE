"""A person decides; approval runs the action under the row's scope."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from autune_agent.main.actions import Action
from autune_agent.main.pending import (
    NotAnApproverError,
    PendingDecidedError,
    PendingNotFoundError,
    approve,
    queue_l2,
    reject,
)
from autune_agent.models import AgentApprover, AgentPendingAction, AgentRun
from autune_agent.results import ProposedAction
from autune_core import Meeting, Team, TeamMember
from autune_core.errors import PrivacyViolationError


def _pending(session: Session, team: dict[str, str], **kw: Any) -> AgentPendingAction:
    row = AgentPendingAction(
        team_id=team["team"],
        meeting_id=team["meeting"],
        subagent="research",
        tool=kw.pop("tool", "fake.share"),
        kind="share",
        arguments=kw.pop("arguments", {"document_id": "rdoc_1"}),
        evidence=["rdoc_1"],
        scope=kw.pop("scope", "research"),
        **kw,
    )
    session.add(row)
    session.flush()
    return row


def _approver(session: Session, team: dict[str, str], scope: str = "research") -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["member"], scope=scope))
    session.flush()


def _share(calls: list[dict[str, Any]], result: dict[str, Any] | None = None) -> Action:
    def share(session: Any, team_id: str, document_id: str) -> dict[str, Any]:
        calls.append({"team_id": team_id, "document_id": document_id})
        return result or {"ok": True, "summary": "공유", "evidence": [document_id]}

    return Action("fake.share", share, "L2")


def test_approval_runs_the_action_under_the_rows_scope(
    session: Session, team: dict[str, str]
) -> None:
    _approver(session, team)
    row = _pending(session, team)
    calls: list[dict[str, Any]] = []

    done = approve(session, row.id, user_id=team["member"], actions={"fake.share": _share(calls)})

    assert calls == [{"team_id": team["team"], "document_id": "rdoc_1"}]
    assert (done.status, done.result_ok, done.decided_by) == ("approved", True, team["member"])
    assert done.decided_at is not None


def test_an_any_approver_decides_every_scope(session: Session, team: dict[str, str]) -> None:
    _approver(session, team, scope="any")
    row = _pending(session, team, scope="workload")

    assert (
        approve(session, row.id, user_id=team["member"], actions={"fake.share": _share([])}).status
        == "approved"
    )


def test_the_wrong_scope_is_refused_and_nothing_runs(
    session: Session, team: dict[str, str]
) -> None:
    _approver(session, team, scope="workload")
    row = _pending(session, team)
    calls: list[dict[str, Any]] = []

    with pytest.raises(NotAnApproverError):
        approve(session, row.id, user_id=team["member"], actions={"fake.share": _share(calls)})
    assert calls == []


def test_a_second_decision_is_a_conflict(session: Session, team: dict[str, str]) -> None:
    _approver(session, team)
    row = _pending(session, team)
    approve(session, row.id, user_id=team["member"], actions={"fake.share": _share([])})

    with pytest.raises(PendingDecidedError):
        reject(session, row.id, user_id=team["member"], reason="not_now")


def test_an_unknown_or_foreign_id_is_not_found_without_echo(
    session: Session, team: dict[str, str]
) -> None:
    with pytest.raises(PendingNotFoundError) as caught:
        approve(session, "pa_nobody", user_id=team["member"], actions={})
    assert "pa_nobody" not in str(caught.value)


def test_a_failed_action_is_recorded_with_this_layers_reason(
    session: Session, team: dict[str, str]
) -> None:
    _approver(session, team)
    row = _pending(session, team)
    gone = _share([], {"ok": False, "reason": "document not found: rdoc_1 김", "summary": "x"})

    done = approve(session, row.id, user_id=team["member"], actions={"fake.share": gone})

    assert (done.status, done.result_ok, done.result_reason) == ("failed", False, None)


def test_an_undeclared_tool_fails_cleanly(session: Session, team: dict[str, str]) -> None:
    _approver(session, team)
    row = _pending(session, team, tool="fake.gone")

    done = approve(session, row.id, user_id=team["member"], actions={})

    assert (done.status, done.result_reason) == ("failed", "not a declared action")


def test_a_privacy_violation_propagates(session: Session, team: dict[str, str]) -> None:
    _approver(session, team)
    row = _pending(session, team)

    def leaks(session: Any, team_id: str, document_id: str) -> dict[str, Any]:
        raise PrivacyViolationError("refused")

    with pytest.raises(PrivacyViolationError):
        approve(
            session,
            row.id,
            user_id=team["member"],
            actions={"fake.share": Action("fake.share", leaks, "L2")},
        )


def test_reject_takes_a_fixed_reason_only(session: Session, team: dict[str, str]) -> None:
    _approver(session, team)
    row = _pending(session, team)

    with pytest.raises(ValueError):
        reject(session, row.id, user_id=team["member"], reason="김 팀장이 싫어함")
    done = reject(session, row.id, user_id=team["member"], reason="handled_elsewhere")
    assert (done.status, done.reject_reason) == ("rejected", "handled_elsewhere")


def test_another_teams_row_reads_as_missing(session: Session, team: dict[str, str]) -> None:
    _approver(session, team)
    other = Team(name="다른 팀")
    session.add(other)
    session.flush()
    meeting = Meeting(team_id=other.id, title="다른 회의")
    session.add(meeting)
    session.flush()
    foreign = _pending(session, {"team": other.id, "meeting": meeting.id})
    calls: list[dict[str, Any]] = []

    with pytest.raises(PendingNotFoundError):
        approve(session, foreign.id, user_id=team["member"], actions={"fake.share": _share(calls)})
    assert calls == []


def test_a_removed_member_with_an_approver_row_reads_as_missing(
    session: Session, team: dict[str, str]
) -> None:
    _approver(session, team)
    row = _pending(session, team)
    session.query(TeamMember).filter_by(team_id=team["team"], user_id=team["member"]).delete()
    session.flush()
    calls: list[dict[str, Any]] = []

    with pytest.raises(PendingNotFoundError):
        approve(session, row.id, user_id=team["member"], actions={"fake.share": _share(calls)})
    assert calls == []


def test_a_raise_after_the_claim_leaves_the_row_approved_not_pending(
    session: Session, team: dict[str, str]
) -> None:
    _approver(session, team)
    row = _pending(session, team)
    row_id = row.id

    def leaks(session: Any, team_id: str, document_id: str) -> dict[str, Any]:
        raise PrivacyViolationError("refused")

    with pytest.raises(PrivacyViolationError):
        approve(
            session,
            row_id,
            user_id=team["member"],
            actions={"fake.share": Action("fake.share", leaks, "L2")},
        )
    session.rollback()

    after = session.get(AgentPendingAction, row_id)
    assert after is not None
    assert (after.status, after.result_ok) == ("approved", None)


def test_a_plain_exception_is_recorded_as_failed(session: Session, team: dict[str, str]) -> None:
    _approver(session, team)
    row = _pending(session, team)

    def breaks(session: Any, team_id: str, document_id: str) -> dict[str, Any]:
        raise RuntimeError("boom 김")

    done = approve(
        session,
        row.id,
        user_id=team["member"],
        actions={"fake.share": Action("fake.share", breaks, "L2")},
    )

    assert (done.status, done.result_ok, done.result_reason) == (
        "failed",
        False,
        "the action failed",
    )


def test_an_approved_action_records_the_approver_not_the_proposal(
    session: Session, team: dict[str, str]
) -> None:
    """#862: an approver answers for the decision, so a write that records who
    made it records them, whoever the proposal names."""
    _approver(session, team)
    row = _pending(session, team, arguments={"document_id": "rdoc_1", "user_id": "usr_other"})
    calls: list[dict[str, Any]] = []

    def share(session: Any, team_id: str, document_id: str, user_id: str) -> dict[str, Any]:
        calls.append({"user_id": user_id})
        return {"ok": True, "summary": "공유", "evidence": [document_id]}

    done = approve(
        session,
        row.id,
        user_id=team["member"],
        actions={"fake.share": Action("fake.share", share, "L2")},
    )

    assert done.status == "approved"
    assert calls == [{"user_id": team["member"]}]


def test_a_team_chat_proposal_is_approved_under_the_meeting_it_named(
    session: Session, team: dict[str, str]
) -> None:
    """#862: queued from the team screen, the row is the named meeting's, so
    approval binds that meeting even for a write that takes it from the scope."""
    _approver(session, team, scope="any")
    chat = AgentRun(
        team_id=team["team"],
        meeting_id=None,
        trigger={"kind": "chat"},
        outcome="answered",
        route="report",
    )
    session.add(chat)
    session.flush()
    proposal = ProposedAction(
        kind="publish",
        title="t",
        tool="fake.publish",
        level="L2",
        rationale="r",
        arguments={"meeting_id": team["meeting"], "draft_id": "rdr_1"},
    )
    queue_l2(session, actions={}, run=chat, proposed=[proposal])
    row = session.query(AgentPendingAction).one()
    calls: list[str] = []

    def publish(session: Any, team_id: str, meeting_id: str, draft_id: str) -> dict[str, Any]:
        calls.append(meeting_id)
        return {"ok": True, "summary": "게시", "evidence": [draft_id]}

    done = approve(
        session,
        row.id,
        user_id=team["member"],
        actions={"fake.publish": Action("fake.publish", publish, "L2")},
    )

    assert (done.status, done.meeting_id, calls) == ("approved", team["meeting"], [team["meeting"]])
