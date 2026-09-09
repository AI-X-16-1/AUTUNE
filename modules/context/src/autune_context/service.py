"""Business logic for the Meeting Context Engine (``autune_context``).

Owner: 문민재. See docs/modules/context.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``ctx_*`` tables.
Never imports another module.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from autune_context.models import CtxDecision, CtxDecisionVersion


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
    that exists. It is a global, idempotent sweep, safe to run at any time.
    """
    live_threads = select(CtxDecisionVersion.thread_id).distinct()
    orphans = session.scalars(
        select(CtxDecision.id).where(CtxDecision.id.not_in(live_threads))
    ).all()
    if orphans:
        session.execute(delete(CtxDecision).where(CtxDecision.id.in_(orphans)))
    return len(orphans)
