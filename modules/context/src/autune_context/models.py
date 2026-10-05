"""Tables owned by module D.

Every table name starts with ``ctx_``. Foreign keys may reference shared
entities (``meetings.id``, ``teams.id``) but never another module's tables.
Each table needs a path to deletion by ``meeting_id`` or ``team_id`` — see
``autune_context.service`` for the two cases cascade does not cover.

See docs/architecture/data-model.md and docs/modules/context.md.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
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

from autune_context.constants import EMBEDDING_DIM
from autune_core import Base, ids, new_id

_CHANGE_TYPES = ("unchanged", "modified", "reversed", "new")
_NLI_LABELS = ("entailment", "contradiction", "neutral")
_EMBEDDING_KINDS = ("topic", "material")
_LINK_STATUSES = ("asserted", "pending", "confirmed", "rejected")
_BRIEF_MATCHES = ("series", "topic", "latest")


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
        # Declared to match the raw-SQL index the migration actually creates —
        # without this, autogenerate sees no model-side index and emits a
        # drop_index for it. The migration itself is left as raw SQL. See #130.
        Index(
            "ix_ctx_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ref_label: Mapped[str] = mapped_column(String(400), nullable=False)
    """The topic label this vector represents, for debugging and for BM25 pairing."""
    utterance_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    """The ``utterances.id`` values this topic segment was cut from, in
    transcript order. The re-ranker reads their text back from ``utterances``
    rather than from a copy here, so masking and retention stay A's and the
    meeting cascade's. ``None`` on rows written before this column existed;
    those re-rank against ``ref_label`` instead."""
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
    linked_meeting_date: Mapped[date | None] = mapped_column(Date)
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
    source_utterance_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    """The ``utterances.id`` values B drew the decision from. Ids only, so a
    person deleting their own speech (#587) finds the statements that came from
    it. ``None`` on rows written before this column existed, and those are treated
    as possibly theirs; ``[]`` is a decision B cited no line for."""
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
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set once ``tasks.notify_context_events`` claims this meeting's topic-link
    and drift notices, *before* any are sent. Guards against a duplicate post
    if the task is redelivered, at the cost of a notice going unsent (never
    retried) if the worker dies between the claim and the send. Same
    trade-off ``published_at`` already makes for the publish step."""
    late_drift_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set once ``tasks.notify_late_drift`` claims this meeting's catch-up
    drift notice, *before* it is sent, and cleared -- there is nothing left to
    claim -- alongside it. Only relevant for a meeting whose lineage arrived
    after ``notified_at`` already fired -- see ``late_drift_due_at`` and
    ``publish_if_ready(force=...)``."""
    late_drift_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set by ``service.build_decision_lineage`` in the same transaction as
    ``extraction_seen`` when this run's lineage is late (arrived after a
    B-timeout publish). ``extraction_seen`` flips exactly once, so a naive
    "was this run late" check reads False on a Celery redelivery of
    ``on_extraction_completed`` that lands after the first run's commit but
    before its ``publish_if_ready.delay(force=True)`` -- the drift warning
    that redelivery owes would otherwise be silently lost. This column
    survives across such a redelivery instead: ``build_decision_lineage``
    returns ``late_drift_due_at is not None`` rather than a freshly computed
    boolean, so the "still owed" state persists on the row until
    ``notify_late_drift`` clears it -- because it claimed the send, or because
    the team has no Slack channel to send to.

    That protects the window between ``build_decision_lineage``'s commit and
    ``notify_late_drift``'s claim. It does not protect the send itself:
    ``notify_late_drift`` sets ``late_drift_notified_at`` and clears this
    column in one commit *before* posting, so a worker that dies after that
    commit and before the post loses the notice for good. That is the
    accept-a-lost-notice-over-a-duplicate trade ``notified_at`` already
    makes, kept here on purpose."""


class CtxBrief(Base, TimestampMixin):
    """The pre-meeting brief for one scheduled meeting: which past meeting it
    recaps, and whether it went out.

    One row per scheduled meeting, inserted when the brief is composed; the
    insert is the claim that keeps overlapping periodic runs from sending it
    twice (``briefs.compose_due_brief``).

    **Stores a choice, never content.** The recap -- the past meeting's topics
    and decisions -- is rendered from that meeting's live rows each time it is
    read. A copied recap would outlive the meeting it quotes, which is the
    retention problem ``CtxDecisionVersion.previous_statement`` already needs a
    sweep for. ``previous_meeting_id`` is ``SET NULL`` on delete, so a recap
    whose meeting was swept reads as gone rather than being reconstructed.
    """

    __tablename__ = "ctx_briefs"
    __table_args__ = (
        CheckConstraint(
            f"match_reason IS NULL OR match_reason IN {_BRIEF_MATCHES!r}",
            name="ck_ctx_briefs_match_reason",
        ),
    )

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    previous_meeting_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="SET NULL")
    )
    match_reason: Mapped[str | None] = mapped_column(String(16))
    """How ``previous_meeting_id`` was chosen: ``series`` (same title),
    ``topic`` (hybrid retrieval over the title and agenda), ``latest`` (the
    team's most recent analyzed meeting). ``None`` when there was nothing to
    choose from -- which, with ``previous_meeting_id`` also ``None``, is how a
    reader tells "no past meeting" from "the past meeting was deleted"."""
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set in the same commit as the row, *before* the Slack post -- the
    duplicate-over-loss trade ``CtxMeetingStatus.notified_at`` makes. ``None``
    when the team has no Slack channel: the brief is still readable in the app."""


class CtxTeamAgenda(Base, TimestampMixin):
    """The latest ``TeamAgenda`` module B published for one team (#436): the open
    Jira issues made from the team's own action items, for the pre-meeting
    brief's agenda (``briefs.agenda_for``).

    One row per team, replaced whole by a newer snapshot. B republishes every
    five minutes and events can arrive out of order, so a snapshot only
    replaces this row when its ``as_of`` is later (``briefs.store_team_agenda``).

    **The one ``ctx_`` table that holds another module's text.** ``issues``
    carries B's item descriptions (masked at write, like every stored
    transcript-derived string), not a choice D made -- unlike ``CtxBrief``,
    which keeps only which meeting it recaps. A copy outlives its source, so
    this one is kept short-lived: B's next snapshot drops an item that was
    deleted, and a snapshot B stopped refreshing is ignored after
    ``AGENDA_STALE_AFTER`` and deleted by ``send_due_briefs``
    (``briefs.purge_stale_agendas``). Goes with the team (``CASCADE``).
    """

    __tablename__ = "ctx_team_agendas"

    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """When B read it, from the payload -- not when D stored it."""
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    """``AgendaIssue`` dumps (``title``, ``key``, ``status``, ``url``) in B's
    order, most pressing first. Validated by the contract before storing."""
