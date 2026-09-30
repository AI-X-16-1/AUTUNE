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
    reject,
)
from autune_agent.models import AgentApprover, AgentPendingAction
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
