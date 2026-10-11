"""``agent.live_research_notes``: what was looked up while a meeting ran.

Research reads it through its Toolbox, like any tool, when module B labelled no
question in the meeting: the answer to "what did this meeting leave open" is
then the notes already written during it, not "nothing to research".
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_agent.models import AgentLiveResearch
from autune_core import Meeting

MAX_NOTES = 5


def live_research_notes(session: Session, team_id: str, meeting_id: str) -> dict[str, Any]:
    """Use this to list what was already looked up during one meeting -- the
    questions researched live and each one's answer line. Not for writing a new
    document.

    Returns the meeting's finished live notes, newest first (at most five): the
    question as the title and the note's first line, its answer, as the body.
    Notes still running or that failed are left out.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None or meeting.team_id != team_id:
        return {
            "ok": False,
            "reason": "meeting not found",
            "summary": "그 회의를 찾을 수 없습니다.",
            "confidence": 0.0,
        }
    docs = list(
        session.scalars(
            sa.select(AgentLiveResearch)
            .where(AgentLiveResearch.meeting_id == meeting_id, AgentLiveResearch.status == "done")
            .order_by(AgentLiveResearch.created_at.desc())
        )
    )
    items = [
        {
            "title": doc.question,
            "body": next(
                (line.strip() for line in (doc.body or "").splitlines() if line.strip()), ""
            ),
            "id": doc.id,
            "score": 1.0,
        }
        for doc in docs[:MAX_NOTES]
    ]
    return {
        "ok": True,
        "summary": f"회의 중 조사 {len(docs)}건",
        "items": items,
        "evidence": [i["id"] for i in items],
        "truncated": len(docs) > MAX_NOTES,
    }
