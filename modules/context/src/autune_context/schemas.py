"""Internal schemas for module D.

Anything another module needs belongs in ``packages/contracts``, not here.
These are the response bodies (and one request body) for this module's own
read API. No other service parses them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class TopicLinkRead(BaseModel):
    """One link from this meeting's topic to a past meeting that discussed it.

    ``linked_meeting_id`` is ``None`` once the linked meeting has been deleted
    by the retention sweep (``ON DELETE SET NULL``) — the link row survives so
    the UI can show "the linked meeting is gone" rather than reconstructing its
    content from the embedding. ``linked_meeting_date`` deliberately survives
    the same deletion (see docs/modules/context.md, "Deletion" — a date is not
    reconstructed content), so it can still be non-``None`` even when
    ``linked_meeting_id`` is not.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    meeting_id: str
    topic_label: str
    linked_meeting_id: str | None
    linked_meeting_date: datetime | None
    similarity: float
    rerank_score: float
    confidence: float
    status: str


class TopicLinksRead(BaseModel):
    """A meeting's topic links, split the way S22 renders them.

    ``asserted`` covers both ``asserted`` (above threshold) and ``confirmed``
    (a ``pending`` link the user accepted) — both are shown as settled.
    ``rejected`` links are dismissed and are not returned in either list.
    """

    asserted: list[TopicLinkRead]
    pending: list[TopicLinkRead]


class LinkConfirmRequest(BaseModel):
    """A user's decision on a ``pending`` link."""

    status: Literal["confirmed", "rejected"]


class DecisionVersionRead(BaseModel):
    """One version of a decision, as seen in one meeting.

    ``previous_statement``/``previous_meeting_id`` are blanked by the router
    (not read as-is off the row) once the meeting they quote has left the
    retention window — see ``router.get_decision_thread``.

    Deliberately omits ``CtxDecisionVersion.key_stakeholders_absent``. That
    field still goes out on the ``ContextLinks`` event to E and in the
    decision-drift Slack DM to the absent person, but no route under
    ``/api/context`` checks who is asking (#156), so a human-facing GET would
    hand any caller a cross-meeting roster of one person's absences — see
    #188. Re-add it here once #156 ships route auth.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    meeting_id: str
    current_statement: str
    previous_statement: str | None
    previous_meeting_id: str | None
    change_type: str
    nli_label: str | None
    confidence: float
    created_at: datetime


class DecisionLineageRead(BaseModel):
    """A thread's full timeline, oldest version first.

    ``topic_label`` is derived from the latest *visible* version's own
    statement (by ``router.get_decision_thread``), not read off the thread's
    cached ``topic_label`` column — see that function's docstring.
    """

    thread_id: str
    topic_label: str
    versions: list[DecisionVersionRead]


class DecisionSummaryRead(BaseModel):
    """One thread's current head, for the team-wide decisions list.

    Mirrors ``DecisionVersionRead`` for the head version only — a caller after
    the full drift history opens the thread with ``GET /decisions/{thread_id}``.
    ``topic_label`` is derived the same way as ``DecisionLineageRead``'s.
    """

    model_config = ConfigDict(from_attributes=True)

    thread_id: str
    topic_label: str
    meeting_id: str
    change_type: str
    confidence: float
    updated_at: datetime
