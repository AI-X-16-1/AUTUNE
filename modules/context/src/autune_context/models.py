"""Tables owned by module D.

Every table name starts with ``ctx_``. Foreign keys may reference shared
entities (``meetings.id``, ``teams.id``) but never another module's tables.
Each table needs a path to deletion by ``meeting_id`` or ``team_id`` — see
``autune_context.service`` for the two cases cascade does not cover.

See docs/architecture/data-model.md and docs/modules/context.md.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
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

from autune_context.constants import EMBEDDING_DIM
from autune_core import Base, ids, new_id

_CHANGE_TYPES = ("unchanged", "modified", "reversed", "new")
_NLI_LABELS = ("entailment", "contradiction", "neutral")
_EMBEDDING_KINDS = ("topic", "material")
_LINK_STATUSES = ("asserted", "pending", "confirmed", "rejected")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CtxEmbedding(Base, TimestampMixin):
    """A topic (or, in Phase 2, a material chunk) embedded for cross-meeting search.

    Derived from masked text. Cascades from ``meetings.id`` so it never outlives
    its meeting — an embedding that does is a retention violation.
    """

    __tablename__ = "ctx_embeddings"
    __table_args__ = (
        CheckConstraint(f"kind IN {_EMBEDDING_KINDS!r}", name="ck_ctx_embeddings_kind"),
        Index("ix_ctx_embeddings_meeting_id", "meeting_id"),
        # The HNSW index on `embedding` is created in the migration with raw SQL
        # (vector_cosine_ops); SQLAlchemy autogenerate does not model it.
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ref_label: Mapped[str] = mapped_column(String(400), nullable=False)
    """The topic label this vector represents, for debugging and for BM25 pairing."""
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)


class CtxTopicLink(Base, TimestampMixin):
    """A link from a topic in this meeting to a past meeting that discussed it.

    ``linked_meeting_id`` is ``SET NULL`` on delete: a retention sweep may remove
    the past meeting, and the UI then shows "the linked meeting is gone" rather
    than reconstructing it from an embedding.
    """

    __tablename__ = "ctx_topic_links"
    __table_args__ = (
        CheckConstraint(f"status IN {_LINK_STATUSES!r}", name="ck_ctx_topic_links_status"),
        Index("ix_ctx_topic_links_meeting_id", "meeting_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    topic_label: Mapped[str] = mapped_column(String(400), nullable=False)
    linked_meeting_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="SET NULL")
    )
    linked_meeting_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    rerank_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    retriever_version: Mapped[str] = mapped_column(String(200), nullable=False)
    reranker_version: Mapped[str] = mapped_column(String(200), nullable=False)


class CtxDecision(Base, TimestampMixin):
    """A decision thread: the lineage identity that persists across meetings.

    Anchored on ``team_id``, not on a meeting: a thread must survive its origin
    meeting reaching the retention window. A thread whose last version has been
    deleted is removed by the deletion hook in ``autune_context.service``.
    """

    __tablename__ = "ctx_decisions"
    __table_args__ = (Index("ix_ctx_decisions_team_id", "team_id"),)

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id(ids.DECISION_THREAD)
    )
    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    topic_label: Mapped[str] = mapped_column(String(400), nullable=False)


class CtxDecisionVersion(Base, TimestampMixin):
    """One version of a decision, as seen in one meeting.

    ``source_decision_id`` (``dec_``) is B's — stored as a plain string, no FK.
    ``previous_meeting_id`` is unconstrained on purpose: a retention sweep on that
    meeting must not cascade into this thread. ``previous_statement`` is a
    verbatim copy of that meeting's decision text, though, and must not outlive
    it — ``service.sweep_dangling_previous_statements`` nulls it once
    ``previous_meeting_id`` no longer resolves. See docs/modules/context.md,
    "Deletion".
    """

    __tablename__ = "ctx_decision_versions"
    __table_args__ = (
        CheckConstraint(
            f"change_type IN {_CHANGE_TYPES!r}", name="ck_ctx_decision_versions_change_type"
        ),
        CheckConstraint(
            f"nli_label IS NULL OR nli_label IN {_NLI_LABELS!r}",
            name="ck_ctx_decision_versions_nli_label",
        ),
        Index("ix_ctx_decision_versions_thread_id", "thread_id"),
        Index("ix_ctx_decision_versions_meeting_id", "meeting_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ctx_decisions.id", ondelete="CASCADE"), nullable=False
    )
    source_decision_id: Mapped[str] = mapped_column(String(64), nullable=False)
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    previous_version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ctx_decision_versions.id", ondelete="SET NULL")
    )
    current_statement: Mapped[str] = mapped_column(Text, nullable=False)
    previous_statement: Mapped[str | None] = mapped_column(Text)
    previous_meeting_id: Mapped[str | None] = mapped_column(String(64))
    change_type: Mapped[str] = mapped_column(String(16), nullable=False)
    nli_label: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    key_stakeholders_absent: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    nli_version: Mapped[str] = mapped_column(String(200), nullable=False)


class CtxMeetingStatus(Base, TimestampMixin):
    """Completion tracking for D's two halves, plus the B-timeout deadline.

    One row per meeting. ``publish_if_ready`` reads it to decide whether to
    publish, and ``published_at`` makes the publish idempotent.
    """

    __tablename__ = "ctx_meeting_status"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    topic_linking_done: Mapped[bool] = mapped_column(nullable=False, default=False)
    lineage_done: Mapped[bool] = mapped_column(nullable=False, default=False)
    extraction_seen: Mapped[bool] = mapped_column(nullable=False, default=False)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
