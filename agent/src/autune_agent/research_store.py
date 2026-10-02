"""Research documents: the save tool (L0) and the share action (L2). Spec section 4 ②.

Both return ``ToolResult``-shaped dicts, like a module's tools, and take
``team_id`` from the run's scope (``bind_scope``), never from the model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Utterance
from autune_integrations import assert_masked

from .models import AgentResearchDocument, AgentResearchSource


def _refused(reason: str, summary: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "summary": summary, "confidence": 0.0}


def save_research_document(
    session: Session, team_id: str, meeting_id: str, body: str, utterance_ids: list[str]
) -> dict[str, Any]:
    """Use this once, at the end of a Research run, to keep the document it wrote
    for a person to approve. Not for anything a person has not asked to be kept.

    Keeps ``body`` as the meeting's one proposed document, replacing an earlier
    proposal that nobody has decided on. Raises ``PrivacyViolationError``,
    writing nothing, when ``body`` still holds personal data. Its sources are the meetings of
    ``utterance_ids`` and the meeting itself. Refuses, writing nothing, when any
    of ``utterance_ids`` no longer exists or is not this team's.
    """
    if not body.strip():
        return _refused("empty document", "빈 문서는 저장하지 않습니다.")
    # The body is a model's text: it can repeat a value the masker missed or
    # make one up. Raised, not refused -- a privacy violation fails the run.
    assert_masked(body, destination="agent_research_documents")
    meeting = session.get(Meeting, meeting_id)
    if meeting is None or meeting.team_id != team_id:
        return _refused("meeting not found", "그 회의를 찾을 수 없습니다.")
    wanted = set(utterance_ids)
    found = session.execute(
        sa.select(Utterance.id, Utterance.meeting_id)
        .join(Meeting, Meeting.id == Utterance.meeting_id)
        .where(Utterance.id.in_(list(wanted)), Meeting.team_id == team_id)
    ).all()
    if len(found) < len(wanted):
        # Fail closed: an id that is gone (its meeting was reprocessed or deleted
        # since the search) or belongs to another team would leave its words in
        # the body without its meeting as a source, and the document would
        # outlive that meeting.
        return _refused("source changed", "인용한 발언이 바뀌어 저장하지 않았습니다.")
    sources = {m for _, m in found} | {meeting_id}
    doc = session.scalars(
        sa.select(AgentResearchDocument).where(
            AgentResearchDocument.meeting_id == meeting_id,
            AgentResearchDocument.status == "proposed",
        )
    ).first()
    if doc is None:
        doc = AgentResearchDocument(team_id=team_id, meeting_id=meeting_id, body=body)
        session.add(doc)
        session.flush()
        new = sources
    else:
        # Sources are only ever added on overwrite, never dropped: deleting a
        # source row fires the trigger that deletes the document itself. A
        # meeting an earlier draft quoted stays a source, which errs toward
        # deleting the document sooner, never later.
        doc.body = body
        existing = set(
            session.scalars(
                sa.select(AgentResearchSource.meeting_id).where(
                    AgentResearchSource.document_id == doc.id
                )
            )
        )
        new = sources - existing
    session.add_all(AgentResearchSource(document_id=doc.id, meeting_id=m) for m in new)
    session.flush()
    return {
        "ok": True,
        "summary": f"리서치 문서를 저장했습니다. 출처 회의 {len(sources)}개.",
        "evidence": [doc.id],
    }


def share_research_document(session: Session, team_id: str, document_id: str) -> dict[str, Any]:
    """Share an approved research document with the team: it appears on the
    meeting page for every member. L2 -- runs only after a person approves."""
    doc = session.get(AgentResearchDocument, document_id)
    if doc is None or doc.team_id != team_id:
        return _refused("document not found", "그 문서를 찾을 수 없습니다.")
    if doc.status != "proposed":
        return _refused("already decided", "이미 결정된 문서입니다.")
    doc.status = "approved"
    doc.decided_at = datetime.now(UTC)
    session.flush()
    return {"ok": True, "summary": "리서치 문서를 팀에 공유했습니다.", "evidence": [doc.id]}
