"""A person deleted their own speech: live documents fed by it go (spec section 4, #1014).

A live document's words came from live rows, which have no utterance id, so
the document cannot be traced to one line. Every live document *of* a meeting
the person spoke in goes, and every one that *quotes* such a meeting -- erring
toward deleting more, as E's hook does. Registered from ``router`` because A's
deletion runs in the API process, which imports every router and no tasks
module. Safe to run twice. Ids and counts only in the log.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, delete, or_, select
from sqlalchemy.orm import Session

from autune_agent.models import AgentLiveResearch, AgentLiveResearchSource
from autune_core import Utterance, session_scope
from autune_core.deletion import on_speech_deleted

log = logging.getLogger(__name__)


def forget_live_research(session: Session, utterance_ids: Sequence[str]) -> int:
    meetings = select(Utterance.meeting_id).where(Utterance.id.in_(list(utterance_ids)))
    quoting = select(AgentLiveResearchSource.document_id).where(
        AgentLiveResearchSource.meeting_id.in_(meetings)
    )
    result = cast(
        CursorResult[Any],
        session.execute(
            delete(AgentLiveResearch)
            .where(
                or_(AgentLiveResearch.meeting_id.in_(meetings), AgentLiveResearch.id.in_(quoting))
            )
            .execution_options(synchronize_session=False)
        ),
    )
    session.flush()
    return int(result.rowcount or 0)


@on_speech_deleted("agent_live")
def forget_deleted_speech(user_id: str, utterance_ids: Sequence[str]) -> None:
    with session_scope() as session:
        gone = forget_live_research(session, utterance_ids)
    log.info(
        "agent_live_speech_forgotten user_id=%s utterances=%d documents=%d",
        user_id,
        len(utterance_ids),
        gone,
    )
