"""Who decides a team's L2 proposals -- the writer behind 설정 › 승인자 (#592).

``packages/core`` has no team administrator on purpose (agent-layer.md section
5), so the layer defines who may change this list itself:

- While no current member holds ``any``, any member may change the list. A
  new team would otherwise have no way in, and neither would a team whose
  ``any`` approver left while others kept narrower scopes.
- After that, only an approver with scope ``any`` may change it.
- A change is saved only if, afterwards, no rows remain or one of them is
  ``any``. So the first assignment holds ``any``, clearing every row returns
  the team to the start, and narrower scopes with no ``any`` are never
  written -- that state arises only when the ``any`` approver leaves, which
  the first rule reopens.

Rows of a former member count for nothing here, as in ``approver_scopes``: a
team whose only ``any`` approver left is open to its members again.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from autune_agent.models import APPROVER_SCOPES, AgentApprover
from autune_core import TeamMember, User
from autune_core.errors import AutuneError, ConflictError, NotFoundError, PermissionDeniedError

from .pending import approver_scopes

SCOPE_ORDER = ("any", "report", "research", "followup", "workload")
"""``APPROVER_SCOPES`` in the order the settings screen shows them: the manager first."""
assert set(SCOPE_ORDER) == set(APPROVER_SCOPES)


class NotAManagerError(PermissionDeniedError):
    def __init__(self) -> None:
        super().__init__("only an approver with scope 'any' may change the approvers")


class LastManagerError(ConflictError):
    def __init__(self) -> None:
        super().__init__("the team must keep at least one approver with scope 'any'")


class MemberNotFoundError(NotFoundError):
    def __init__(self) -> None:
        # Never the id: it is whatever the caller sent.
        AutuneError.__init__(self, "team member not found", resource="team member")


def _member_rows(session: Session, team_id: str) -> list[tuple[str, str]]:
    """``(user_id, scope)`` of every approver row held by a current member."""
    return [
        (user_id, scope)
        for user_id, scope in session.execute(
            select(AgentApprover.user_id, AgentApprover.scope)
            .join(
                TeamMember,
                (TeamMember.team_id == AgentApprover.team_id)
                & (TeamMember.user_id == AgentApprover.user_id),
            )
            .where(AgentApprover.team_id == team_id)
        )
    ]


def can_manage(session: Session, team_id: str, user_id: str) -> bool:
    if not any(scope == "any" for _, scope in _member_rows(session, team_id)):
        # Keyed on ``any``, not on "no rows": when the one ``any`` approver
        # leaves or deletes their account while another member keeps
        # ``report``, nobody could change the list again (#621 review).
        return True
    return "any" in approver_scopes(session, team_id, user_id)


def list_members(session: Session, team_id: str) -> list[tuple[str, str, list[str]]]:
    """``(user_id, name, scopes)`` for every member, by name; scopes in ``SCOPE_ORDER``."""
    held: dict[str, set[str]] = {}
    for user_id, scope in _member_rows(session, team_id):
        held.setdefault(user_id, set()).add(scope)
    members = session.execute(
        select(User.id, User.display_name)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team_id)
        .order_by(User.display_name, User.id)
    )
    return [
        (user_id, name, [s for s in SCOPE_ORDER if s in held.get(user_id, set())])
        for user_id, name in members
    ]


def set_scopes(
    session: Session, *, team_id: str, user_id: str, scopes: Iterable[str], by: str
) -> list[str]:
    """Replace ``user_id``'s approver scopes in ``team_id``; the caller commits.

    The caller has been checked to be a member. Scopes are validated by the
    request model; an unknown one here is a programming error. Holds a row lock
    on the team's approvers until the caller commits.
    """
    wanted = set(scopes)
    if not wanted <= set(APPROVER_SCOPES):
        raise ValueError("unknown approver scope")
    # Lock the team's rows before reading them: two `any` approvers removing
    # each other's `any` at once would otherwise both pass the last-manager
    # check (#621 review). A team with no rows locks nothing; a write there must
    # leave an `any` row behind, so two at once cannot strand the team.
    session.execute(
        select(AgentApprover.user_id).where(AgentApprover.team_id == team_id).with_for_update()
    )
    if not can_manage(session, team_id, by):
        raise NotAManagerError()
    is_member = session.scalar(
        select(TeamMember.id).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    )
    if is_member is None:
        raise MemberNotFoundError()

    after = {(u, s) for u, s in _member_rows(session, team_id) if u != user_id}
    after |= {(user_id, s) for s in wanted}
    if after and not any(s == "any" for _, s in after):
        raise LastManagerError()

    session.execute(
        delete(AgentApprover).where(
            AgentApprover.team_id == team_id, AgentApprover.user_id == user_id
        )
    )
    session.add_all(AgentApprover(team_id=team_id, user_id=user_id, scope=s) for s in wanted)
    session.flush()
    return [s for s in SCOPE_ORDER if s in wanted]
