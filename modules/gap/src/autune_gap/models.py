"""Tables owned by module C.

Every table name starts with ``gap_``. Foreign keys may reference shared
entities (``meetings.id``, ``utterances.id``, ``participants.id``) but never
another module's tables. Everything here reaches deletion through
``meetings.id``, so no deletion hook is needed — see docs/modules/gap.md.

The topic graph is stored as rows and loaded into NetworkX per run: one
meeting is tens of nodes, and ADR 0005 records why that is not a graph
database.

See docs/architecture/data-model.md and docs/modules/gap.md.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from autune_core import Base, ids, new_id

_SEVERITIES = ("high", "medium", "low")
"""Mirrors ``autune_contracts.GapSeverity``. Stored as a string with a check
constraint rather than a PostgreSQL enum — adding a value to a PG enum takes a
migration lock (data-model.md, "Conventions")."""


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class GapTopic(Base, TimestampMixin):
    """One node of this meeting's topic graph.

    The id prefix is ``topic_``, which ``autune_contracts.Topic`` requires by
    pattern. A meeting gets a fresh subgraph every run — topics are not
    accumulated across meetings, because cross-meeting linking is module D's
    (issue #23, and docs/modules/gap.md, "The topic graph is per meeting").

    ``centrality`` is PageRank, normalised to 0..1. ``betweenness`` is kept
    beside it rather than folded in: risk scoring weights the two differently,
    and a single blended number could not be re-weighted afterwards without
    recomputing the graph.

    ``label`` is derived from utterance text, which module A masked before the
    first write. There is no unmasked source to re-derive it from.

    ``extractor_version`` is what makes the rest of this comparable. C's metric
    is gap precision measured over time, and dismissals feed threshold tuning —
    both read across model versions, and a row that cannot say which model built
    it cannot take part in either. Added with the table rather than later,
    because a column added afterwards leaves every earlier row unattributable
    forever.
    """

    __tablename__ = "gap_topics"
    __table_args__ = (
        CheckConstraint("centrality >= 0 AND centrality <= 1", name="ck_gap_topics_centrality"),
        CheckConstraint("betweenness >= 0 AND betweenness <= 1", name="ck_gap_topics_betweenness"),
        Index("ix_gap_topics_meeting_id", "meeting_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id(ids.TOPIC))
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(400), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(200), nullable=False)
    """The extractor that produced this node, name and version
    (``ko_core_news_lg-3.8.0``). ``EntityExtractor.model_version`` supplies it.

    ``gap_gaps`` carries no copy: a gap is inferred from topics and reaches this
    through ``gap_related_topics``. Risk scoring is a weighted heuristic whose
    thresholds live in ``config``, not a model — when that becomes one, its
    version belongs on ``gap_gaps`` and not here."""

    centrality: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    betweenness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class GapTopicUtterance(Base):
    """Which utterances a topic was built from, in meeting order.

    A table rather than a JSONB list because the report joins these back to
    read the quotation, and data-model.md rules JSONB out for anything you join
    on. Storing the ids and not the text also means a deleted meeting takes the
    quotation with it — a copy here would leave transcript content behind a
    cascade that no longer reaches it.

    ``position`` keeps meeting order without a second join to ``utterances``.
    """

    __tablename__ = "gap_topic_utterances"
    __table_args__ = (
        UniqueConstraint("topic_id", "utterance_id", name="uq_gap_topic_utterances"),
        Index("ix_gap_topic_utterances_topic_id", "topic_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("gap_topics.id", ondelete="CASCADE"), nullable=False
    )
    utterance_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("utterances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class GapTopicEdge(Base):
    """One relation between two topics of the same meeting.

    Directed: ``source`` acts on ``target``, because the triples relation
    extraction produces are directed and PageRank reads the direction. A
    symmetric relation is written as two rows rather than by dropping the
    direction, so the graph loader never has to guess.

    ``meeting_id`` is carried here as well as on both endpoints. It is
    redundant, and it is what lets the whole graph be loaded with one indexed
    read instead of a join through ``gap_topics``.
    """

    __tablename__ = "gap_topic_edges"
    __table_args__ = (
        CheckConstraint("weight > 0 AND weight <= 1", name="ck_gap_topic_edges_weight"),
        CheckConstraint(
            "source_topic_id <> target_topic_id", name="ck_gap_topic_edges_no_self_loop"
        ),
        UniqueConstraint(
            "source_topic_id", "target_topic_id", "relation", name="uq_gap_topic_edges"
        ),
        Index("ix_gap_topic_edges_meeting_id", "meeting_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    source_topic_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("gap_topics.id", ondelete="CASCADE"), nullable=False
    )
    target_topic_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("gap_topics.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String(100), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)


class GapParticipation(Base):
    """Whether one participant spoke on one topic. Coverage, not volume.

    **``spoke`` is a boolean and must stay one.** Privacy invariant 11 and
    ``privacy.md`` section 3 make speaking ratio private to the speaker, and
    this table is delivered to the whole team inside a gap report. A duration,
    an utterance count, or a share would each turn a coverage matrix into a
    per-person talk-time metric that nobody may see but its subject — and it
    would arrive without anyone deciding to build one, which is how that
    happens.

    ``autune_contracts.Participation`` carries the same shape from the other
    side: ``spoke`` and ``silent`` are lists of ids, with no number attached.

    Silence is stored, not inferred. A participant with no row is one the
    pipeline never considered; a participant with ``spoke=False`` is one it
    considered and found silent, and only the second is evidence of a gap.
    """

    __tablename__ = "gap_participation"
    __table_args__ = (
        UniqueConstraint("topic_id", "participant_id", name="uq_gap_participation"),
        Index("ix_gap_participation_topic_id", "topic_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("gap_topics.id", ondelete="CASCADE"), nullable=False
    )
    participant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("participants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    spoke: Mapped[bool] = mapped_column(nullable=False)


class GapGap(Base, TimestampMixin):
    """One thing the meeting should have covered and did not.

    The id prefix is ``gap_``, which ``autune_contracts.Gap`` requires by
    pattern. The class name repeats the prefix because the table does; renaming
    either would break the mapping between the two.

    ``dismissed_at`` records that somebody called this a false positive. It is
    a real timestamp rather than a soft-delete flag — the row is not hidden,
    it is marked, and ADR 0006's threshold tuning reads the mark. Nobody's id
    is stored with it: which member of a team pressed dismiss is not something
    the feature needs, and storing it would be a per-person record of conduct
    that ADR 0003 refuses.
    """

    __tablename__ = "gap_gaps"
    __table_args__ = (
        CheckConstraint(f"severity IN {_SEVERITIES!r}", name="ck_gap_gaps_severity"),
        CheckConstraint("risk_score >= 0 AND risk_score <= 1", name="ck_gap_gaps_risk_score"),
        UniqueConstraint(
            "meeting_id", "template_key", "template_item_key", name="uq_gap_gaps_template_item"
        ),
        Index("ix_gap_gaps_meeting_id", "meeting_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id(ids.GAP))
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    template_item: Mapped[str | None] = mapped_column(String(400))
    """Which template item went unfilled, when the gap came from a template.
    Null for a gap found from the graph alone.

    Display copy — it is what S20 shows and what ``autune_contracts.Gap``
    carries. Rewording it is an edit to a template file, so it is not what a
    re-run recognises this row by; ``template_item_key`` is."""

    template_key: Mapped[str | None] = mapped_column(String(100))
    """Which domain template raised this — ``general``, ``feature_planning``."""

    template_version: Mapped[str | None] = mapped_column(String(100))
    """Every template file that contributed, with its version:
    ``general.1+feature_planning.1``.

    Recorded for the same reason ``gap_topics.extractor_version`` is. C's metric
    is precision measured over time and dismissals feed threshold tuning; both
    read across template edits, and a row that cannot say which checklist raised
    it averages two different ones together."""

    template_item_key: Mapped[str | None] = mapped_column(String(100))
    """The item's stable key. With ``meeting_id`` and ``template_key`` it is
    this row's natural identity, which is how a re-run finds the gap it already
    raised and leaves ``id`` and ``dismissed_at`` alone — see
    ``service.detect_gaps``. Null for a gap found from the graph alone."""

    suggested_question: Mapped[str | None] = mapped_column(Text)
    """The question that would close this gap.

    A template item carries the question that closes it, so a template gap has
    one from the moment it is raised. #35 makes it specific to the topics the
    gap was inferred from; until then it is the item's own wording."""

    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GapMeetingTemplate(Base, TimestampMixin):
    """Which domain template this meeting is compared against, when somebody
    chose one.

    An override and nothing else: a meeting with no row here gets
    ``config.default_template``. Storing only the exception means changing the
    default changes every meeting that never expressed a preference, which is
    what a default is for.

    **Not** the template itself. Templates are files in this package, versioned
    by git (see ``autune_gap.template`` and #22); this table holds one meeting's
    pointer at one of them, so it cascades from ``meetings.id`` like everything
    else module C owns and needs no deletion hook.

    ``template_key`` carries no foreign key, because the thing it names is a
    file. ``service`` validates it against the loaded templates before writing,
    and a key whose file is later deleted falls back to the default with a
    warning rather than failing the meeting's pipeline.
    """

    __tablename__ = "gap_meeting_template"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    template_key: Mapped[str] = mapped_column(String(100), nullable=False)


class GapRelatedTopic(Base):
    """Which topics a gap was inferred from.

    A table rather than a JSONB list of ids for the same reason as
    ``gap_topic_utterances``: the report joins back to ``gap_topics`` to show
    why the gap was raised, and data-model.md rules JSONB out for anything you
    join on.
    """

    __tablename__ = "gap_related_topics"
    __table_args__ = (
        UniqueConstraint("gap_id", "topic_id", name="uq_gap_related_topics"),
        Index("ix_gap_related_topics_gap_id", "gap_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gap_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("gap_gaps.id", ondelete="CASCADE"), nullable=False
    )
    topic_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("gap_topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
