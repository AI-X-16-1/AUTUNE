"""Tell the approvers that a proposal is waiting for them (#632).

Plan mode queued L2 proposals with nobody told: an approver learned of one only
by opening ``/approvals``. Now a run that leaves pending proposals sends each
approver who may decide one of them a single Slack DM from the team's bot: how
many proposals wait for them, and the link to the page.

**A count and a link, nothing else.** No meeting title, no proposal text, no
names: a DM leaves the system, and what a proposal is about stays behind the
page's sign-in (invariant 11). ``SlackClient`` runs ``check_outbound`` on the
body as every outbound request does.

**One message per run, not per row.** A run that proposes three things sends
one DM per approver. The count is everything waiting for that approver in the
team, not just this run's rows, so the number matches what the page shows.

**Never in the way.** A team without Slack, an approver who has not linked
Slack (#255), or Slack refusing is logged by type and skipped; the run and its
queue are already committed. The person who asked in chat is not messaged
about their own run's proposals: they are reading the answer.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autune_agent.models import AgentApprover, AgentPendingAction, AgentRun
from autune_core import TeamMember, get_settings, load_integration
from autune_core.errors import PrivacyViolationError
from autune_integrations import SlackApi, SlackClient

log = logging.getLogger(__name__)

APPROVALS_PATH = "/approvals"


def notify_approvers(
    session: Session, run: AgentRun, *, slack: SlackApi | None = None
) -> list[str]:
    """DM each approver who may decide one of ``run``'s pending proposals.

    Returns the user ids that were messaged. ``slack`` is for tests; without
    it the team's bot is used, and a team without one is told nothing.
    """
    queued = set(
        session.scalars(
            select(AgentPendingAction.scope).where(
                AgentPendingAction.run_id == run.id, AgentPendingAction.status == "pending"
            )
        )
    )
    if not queued:
        return []
    scopes_of = _approver_scopes(session, run.team_id)
    recipients = sorted(
        user_id
        for user_id, scopes in scopes_of.items()
        if ("any" in scopes or scopes & queued) and user_id != run.requested_by
    )
    if not recipients:
        return []
    if slack is None:
        config = load_integration(session, run.team_id, "slack")
        if config is None:
            log.info("agent_notify_no_slack team_id=%s", run.team_id)
            return []
        slack = SlackClient(config.require_secret())
    link = get_settings().web_base_url.rstrip("/") + APPROVALS_PATH
    sent: list[str] = []
    for user_id in recipients:
        waiting = _waiting_for(session, run.team_id, scopes_of[user_id])
        try:
            slack.send_dm(user_id, f"승인을 기다리는 에이전트 제안이 {waiting}건 있습니다. {link}")
        except PrivacyViolationError:
            raise
        except Exception as exc:  # noqa: BLE001 - one approver's Slack must not stop the others
            log.warning("agent_notify_failed user_id=%s error=%s", user_id, type(exc).__name__)
            continue
        sent.append(user_id)
    return sent


def _approver_scopes(session: Session, team_id: str) -> dict[str, set[str]]:
    """Each current member's approver scopes in the team (``pending.approver_scopes``'s rule)."""
    rows = session.execute(
        select(AgentApprover.user_id, AgentApprover.scope)
        .join(
            TeamMember,
            (TeamMember.team_id == AgentApprover.team_id)
            & (TeamMember.user_id == AgentApprover.user_id),
        )
        .where(AgentApprover.team_id == team_id)
    )
    scopes: dict[str, set[str]] = {}
    for user_id, scope in rows:
        scopes.setdefault(user_id, set()).add(scope)
    return scopes


def _waiting_for(session: Session, team_id: str, scopes: set[str]) -> int:
    query = select(func.count()).where(
        AgentPendingAction.team_id == team_id, AgentPendingAction.status == "pending"
    )
    if "any" not in scopes:
        query = query.where(AgentPendingAction.scope.in_(scopes))
    return int(session.scalar(query) or 0)
