"""Business logic for module D: Meeting Context Engine.

Owner: 문민재. See docs/modules/context.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``ctx_*`` tables.
Never imports another module.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from autune_context.models import CtxDecision, CtxDecisionVersion
from autune_core import deletion, get_logger, session_scope

log = get_logger(__name__)


@deletion.on_meeting_deleted("context")
def cleanup_orphan_decision_threads(meeting_id: str) -> None:
    """Remove decision threads left with no versions after a meeting is deleted.

    Four of D's five tables cascade from ``meetings.id``. ``ctx_decisions`` is
    anchored on ``team_id`` instead, so a lineage survives its origin meeting
    reaching the retention window — but a thread whose *every* version was in the
    deleted meeting is now empty and must go.

    This runs a general orphan sweep, so it must be invoked **after** the
    meeting's rows have been cascade-deleted. ``meeting_id`` is unused: the sweep
    is global and idempotent.
    """
    with session_scope() as session:
        live_threads = select(CtxDecisionVersion.thread_id).distinct()
        orphans = session.scalars(
            select(CtxDecision.id).where(CtxDecision.id.not_in(live_threads))
        ).all()
        if orphans:
            session.execute(delete(CtxDecision).where(CtxDecision.id.in_(orphans)))
            log.info("ctx_orphan_threads_swept", count=len(orphans), after_meeting=meeting_id)
