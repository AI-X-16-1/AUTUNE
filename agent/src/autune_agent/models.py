"""The agent layer's tables (agent-layer.md section 5). Owned by the main agent.

Every table carries the ``agent_`` prefix (invariant 3, ADR 0010), and nobody
outside ``autune_agent`` writes them. Two of them hold copies of meeting content
-- a work item's text, a run's tool steps -- so both cascade from ``meetings``
and go when the meeting goes (privacy.md section 7). ``agent_approvers`` holds a
role assignment, not content, and goes with its user.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from autune_core import Base, new_id
from autune_core.ids import RUN, WORK_ITEM

Json = JSON().with_variant(JSONB(), "postgresql")
"""JSONB on PostgreSQL; plain JSON where unit tests run on SQLite."""

WORK_ITEM_KINDS = ("action", "gap", "open_question", "decision", "risk")
WORK_ITEM_STATUSES = ("open", "in_progress", "blocked", "resolved", "dropped")
APPROVER_SCOPES = ("research", "followup", "workload", "any")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AgentWorkItem(Base):
    """What is true about one piece of work right now, across meetings."""

    __tablename__ = "agent_work_items"
    __table_args__ = (
        CheckConstraint(_in("kind", WORK_ITEM_KINDS), name="ck_agent_work_items_kind"),
        CheckConstraint(_in("status", WORK_ITEM_STATUSES), name="ck_agent_work_items_status"),
        # The one JSONB column filtered on, which data-model.md allows for that reason.
        Index(
            "ix_agent_work_items_origin_utterances",
            "origin_utterances",
            postgresql_using="gin",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id(WORK_ITEM))
    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    """Masked, like everything derived from a transcript."""
    origin_meeting: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    """NULL when the work was not born in a meeting."""
    origin_module: Mapped[str | None] = mapped_column(String(32))
    source_id: Mapped[str | None] = mapped_column(String(64))
    """The owning module's own id (``act_…``, ``dec_…``), so the item is re-read, not trusted."""
    origin_utterances: Mapped[list[str]] = mapped_column(Json, nullable=False, default=list)
    owner_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL")
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """As the owning module's read reported it (section 8 rule 3), never inferred."""
    confidence: Mapped[float | None] = mapped_column(Float)
    """The owning module's value, never one this layer computed (section 10)."""
    external_ref: Mapped[dict[str, Any] | None] = mapped_column(Json)
    last_signal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalation_lv: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    """The whole of "wakes up by itself": the scheduler selects rows due now."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AgentRun(Base):
    """One wake-up, start to finish: why, what it called, what it proposed, what came of it."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id(RUN))
    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    """The deletion path. NULL for a run about no meeting, such as a chat question."""
    requested_by: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL")
    )
    trigger: Mapped[dict[str, Any]] = mapped_column(Json, nullable=False)
    route: Mapped[str | None] = mapped_column(String(32))
    steps: Mapped[list[dict[str, Any]]] = mapped_column(Json, nullable=False, default=list)
    """Tool calls in order: name, ok, evidence ids, truncated. Never tool text."""
    proposed: Mapped[list[dict[str, Any]]] = mapped_column(Json, nullable=False, default=list)
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(Json, nullable=False, default=list)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(Json, nullable=False, default=list)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(Json, nullable=False, default=list)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    """``answered`` | ``unrouted`` | ``budget_exceeded`` | ``failed``."""
    answer: Mapped[str | None] = mapped_column(Text)
    """Not written today (``main/store.py``): an answer may quote another meeting."""
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentApprover(Base):
    """Who "the lead" and "the manager" are for a team (agent-layer.md section 5)."""

    __tablename__ = "agent_approvers"
    __table_args__ = (
        CheckConstraint(_in("scope", APPROVER_SCOPES), name="ck_agent_approvers_scope"),
    )

    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
