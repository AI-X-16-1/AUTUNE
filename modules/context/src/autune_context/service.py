"""Business logic for the Meeting Context Engine (``autune_context``).

Owner: 문민재. See docs/modules/context.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``ctx_*`` tables.
Never imports another module.
"""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from autune_context.models import CtxDecision, CtxDecisionVersion
from autune_core import Meeting


def sweep_orphan_decision_threads(session: Session) -> int:
    """Delete decision threads that have no versions left, returning the count.

    Four of the five ``ctx_*`` tables cascade from ``meetings.id``.
    ``ctx_decisions`` is anchored on ``team_id`` instead, so a lineage survives
    its origin meeting reaching the retention window — but a thread whose *every*
    version was in a since-deleted meeting is now empty and must go.

    Not yet wired into ``autune_core.deletion``: registering a meeting-deletion
    hook that issues a real ``DELETE`` breaks ``packages/core``'s own unit tests,
    which run before migrations on a clean CI database and iterate every
    registered hook (see ADR 0008 and #87). Until #87 lands this is called
    explicitly — by the integration test, and later by the retention sweep once
    that exists. Global and idempotent.
    """
    # Correlated NOT EXISTS, not `id NOT IN (subquery)`: the latter deletes every
    # thread when the versions table is empty. A globally empty versions table
    # is not a sign of trouble — the retention sweep deleting a team's last
    # version is exactly the case this function exists to clean up after.
    has_version = (
        select(CtxDecisionVersion.id).where(CtxDecisionVersion.thread_id == CtxDecision.id).exists()
    )
    orphans = session.scalars(select(CtxDecision.id).where(~has_version)).all()
    if orphans:
        session.execute(delete(CtxDecision).where(CtxDecision.id.in_(orphans)))
    return len(orphans)


def sweep_dangling_previous_statements(session: Session) -> int:
    """Null ``previous_statement`` on versions whose ``previous_meeting_id`` no
    longer exists, returning the count.

    ``previous_meeting_id`` is deliberately unconstrained (see the model
    docstring) so a retention sweep on that meeting does not cascade into an
    unrelated thread's lineage. ``previous_statement`` is a verbatim copy of
    that meeting's decision text, though, and this module refuses exactly this
    for less: ``ctx_embeddings`` cascades so it can never outlive its meeting,
    and a topic link to a deleted meeting is never used to reconstruct content
    (see "Deletion" in docs/modules/context.md). ``change_type``, ``nli_label``
    and ``confidence`` are untouched — the fact that something changed
    survives; only the deleted meeting's wording goes.

    Not yet wired into ``autune_core.deletion``, same reason and same place as
    ``sweep_orphan_decision_threads`` (ADR 0008, #87). Global and idempotent.
    """
    meeting_exists = (
        select(Meeting.id).where(Meeting.id == CtxDecisionVersion.previous_meeting_id).exists()
    )
    dangling = session.scalars(
        select(CtxDecisionVersion.id).where(
            CtxDecisionVersion.previous_meeting_id.is_not(None),
            CtxDecisionVersion.previous_statement.is_not(None),
            ~meeting_exists,
        )
    ).all()
    if dangling:
        session.execute(
            update(CtxDecisionVersion)
            .where(CtxDecisionVersion.id.in_(dangling))
            .values(previous_statement=None)
        )
    return len(dangling)
