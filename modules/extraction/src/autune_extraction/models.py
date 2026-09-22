"""Tables owned by module B.

Every table name starts with ``ext_``. Foreign keys may reference shared
entities (``meetings.id``, ``utterances.id``) but never another module's tables.
Each table needs a path to deletion by meeting_id or user_id.

See docs/architecture/data-model.md.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from autune_core import Base
from autune_core.ids import ACTION_ITEM, DECISION, new_id

from .confirmations import CONFIRMATION_TIMEOUT

ACTION_STATUSES = ("needs_confirmation", "todo", "in_progress", "done")
"""Mirrors ``autune_contracts.ActionStatus``. Stored as a string with a check
constraint rather than a PostgreSQL enum — adding a value to a PG enum takes a
migration lock, a string does not. See data-model.md, Conventions."""

ORIGINS = ("model", "user")
"""Where the item came from. ADR 0006 makes the extraction output a draft the
user completes, so an item added by hand is a first-class row rather than an
anomaly — and telling the two apart is what edit cost is measured against."""

EDIT_KINDS = ("created", "deleted", "edited")
"""What a person did. Not who did it — see ``ExtEditEvent``."""

CONFIRMATION_KINDS = ("commitment", "decision", "concern")
"""What a confirmation button can resolve an ambiguous utterance to.

Mirrors ``confirmations.ACTION_IDS``. Denial lands on ``concern`` rather than
dropping the utterance: a speaker declining to commit is itself something the
meeting said, and module C reads concerns."""

RESOLVED, UNDECIDED, PENDING = "resolved", "undecided", "pending"
"""The three outcomes of a question that was asked. Derived from timestamps,
never stored — see ``ExtConfirmation``."""

NOT_ASKED = "not_asked"
"""An ambiguous agreement nobody has been asked about yet — the DM could not be
sent. Derived like the others: ``sent_at`` is empty."""


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ExtActionItem(Base, TimestampMixin):
    """One trackable commitment, as the user will eventually accept it.

    ADR 0006: the pipeline produces a draft and the user finishes it. So this
    table holds both what the model extracted and what a person typed, and
    ``origin`` is the difference.

    Deleted rows are gone. ``privacy.md`` allows no soft deletes and no
    tombstones holding content, and edit cost does not need one — the counter in
    ``ext_edit_events`` records that a deletion happened, which is all the metric
    asks.
    """

    __tablename__ = "ext_action_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('needs_confirmation','todo','in_progress','done')",
            name="ck_ext_action_items_status",
        ),
        CheckConstraint("origin IN ('model','user')", name="ck_ext_action_items_origin"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_ext_action_items_confidence"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id(ACTION_ITEM)
    )
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)

    assignee_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    """Null until someone is assigned, and null again if that account is deleted.

    Without ``users:read.email`` there is no directory to resolve a Slack account
    against, so this stays null until account linking ships — the state
    ui-spec.md already expects, where item creation is held back rather than
    guessing. See #70.
    """

    assignee_label: Mapped[str | None] = mapped_column(String(200))
    """The name as spoken, kept when it does not resolve to an account."""

    due_date: Mapped[date | None] = mapped_column(Date)
    due_text: Mapped[str | None] = mapped_column(String(100))
    """The words the due date was read from -- "다음 주 금요일" -- which S18 shows
    beside the date so a reader can check the arithmetic. A fragment of the
    masked utterance.

    Cleared when a person sets the date themselves: the phrase no longer
    explains the value, and keeping it would be holding on to the version they
    corrected (#109).
    """

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="needs_confirmation")
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    """A hand-added item is 1.0: a person typing it is the certainty."""

    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="model")

    sources: Mapped[list[ExtActionItemSource]] = relationship(
        back_populates="action_item",
        cascade="all, delete-orphan",
        # Insertion order, which is the order they were handed to us. Ordering
        # here rather than at each read keeps a caller from sorting on ``id``
        # before the rows are flushed, when every id is still None.
        order_by="ExtActionItemSource.id",
    )


class ExtActionItemSource(Base):
    """Which utterances an item came from.

    A table rather than a JSONB list because the drawer joins these back to read
    the quotation, and data-model.md rules JSONB out for anything you join on.

    ADR 0006 makes these load-bearing: they are how a user checks an item without
    replaying the meeting. Whether they survive a member leaving is what ADR 0007
    is deciding.
    """

    __tablename__ = "ext_action_item_sources"
    __table_args__ = (
        UniqueConstraint("action_item_id", "utterance_id", name="uq_ext_action_item_sources"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_item_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ext_action_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    utterance_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("utterances.id", ondelete="CASCADE"), nullable=False, index=True
    )

    action_item: Mapped[ExtActionItem] = relationship(back_populates="sources")


class ExtExternalRef(Base):
    """The page an action item became in an outside tool, once.

    **The primary key is the item and the system**, so an item has at most one
    Notion page. That is the "send once" rule, kept by the database rather than by
    a read-then-write in the sender: a confirmation that reaches two workers, or a
    redelivered task, finds the row there and sends nothing (#30). The same shape
    ``ext_confirmations`` uses for its DM.

    The row is claimed before the call and filled in after it. ``external_id``
    and ``url`` stay empty only inside the sending transaction; a failed call
    rolls the claim back with it, so the next confirmation can try again.

    Deleting the item deletes this row and leaves the Notion page where it is.
    Autune cannot reach into a workspace it only writes to, and a page a team has
    started working in is theirs.
    """

    __tablename__ = "ext_external_refs"
    __table_args__ = (
        CheckConstraint("system IN ('notion','jira')", name="ck_ext_external_refs_system"),
    )

    action_item_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ext_action_items.id", ondelete="CASCADE"), primary_key=True
    )
    system: Mapped[str] = mapped_column(String(16), primary_key=True)
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExtDecisionRef(Base):
    """The page a confirmed decision became in an outside tool, once.

    The same rule as ``ExtExternalRef`` for action items: keyed by the decision and
    the system, claimed before the call, filled in after it. A separate table
    rather than a second key on that one, kept without a foreign key to
    ``ext_decisions`` even though ``build_decisions`` no longer deletes and
    rebuilds every row on a rerun (#297) -- adding one here is a follow-up this
    table's own review did not ask for, not a claim that one would be wrong.
    Keyed by the ``dec_`` id like ``ext_decision_reviews``, and taken with the
    meeting.
    """

    __tablename__ = "ext_decision_refs"
    __table_args__ = (
        CheckConstraint("system IN ('notion','jira')", name="ck_ext_decision_refs_system"),
    )

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    system: Mapped[str] = mapped_column(String(16), primary_key=True)
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExtDecision(Base, TimestampMixin):
    """A decision the meeting settled, as an entity rather than a label.

    ``Classification(kind="decision")`` marks one utterance; a decision usually
    spans several, and module D keys a decision lineage on this row. Dropping it
    or changing its id shape breaks D — see docs/architecture/contracts.md,
    "The B -> D boundary", and packages/contracts/tests/test_decision_boundary.py.

    The id prefix is ``dec_``. D's threads are ``thr_``. The two are deliberately
    not interchangeable: a ``dec_`` id names what *this* meeting settled, a
    ``thr_`` id names the same decision tracked across meetings, and a lineage
    that confused them would claim a meeting decided something it never
    discussed.

    **There is no owner column and there must not be one.** ADR 0007: a record
    reachable by ``meeting_id`` belongs to the meeting and survives the departure
    of whoever spoke it. A decision is the clearest case of that -- the team is
    still bound by it after the person who proposed it leaves.
    """

    __tablename__ = "ext_decisions"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_ext_decisions_confidence"),
        CheckConstraint("origin IN ('model','user')", name="ck_ext_decisions_origin"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id(DECISION))
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    """The decision as settled, in one sentence. PII-masked like every utterance
    it is drawn from — there is no unmasked text to reach this column."""

    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    origin: Mapped[str] = mapped_column(
        String(16), nullable=False, default="model", server_default="model"
    )
    """``model`` for a decision the pipeline proposed, ``user`` for one a person
    added (#246). A rerun rebuilds only the model's: a decision somebody typed is
    not derived from labels, so no rerun can recompute it, and deleting it would
    throw their work away. Same distinction as ``ExtActionItem.origin``."""

    sources: Mapped[list[ExtDecisionSource]] = relationship(
        back_populates="decision", cascade="all, delete-orphan"
    )


class ExtDecisionSource(Base):
    """Which utterances a decision was settled in.

    A table rather than a JSONB list for the same reason as
    ``ext_action_item_sources``: the lineage view joins these back to read the
    quotation, and data-model.md rules JSONB out for anything you join on.

    ``position`` keeps meeting order without a second join to ``utterances``.
    The order is the argument of the decision -- the proposal first, the sentence
    that settles it last -- and sorting by id would scramble it.
    """

    __tablename__ = "ext_decision_sources"
    __table_args__ = (
        UniqueConstraint("decision_id", "utterance_id", name="uq_ext_decision_sources"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ext_decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    utterance_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("utterances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    decision: Mapped[ExtDecision] = relationship(back_populates="sources")


REVIEW_STATUSES = ("pending", "confirmed", "rejected")
"""What a person said about a decision the model proposed (#246). A decision with
no review row is ``pending`` too -- the row exists once somebody touched it."""


class ExtDecisionReview(Base):
    """A person's verdict on one proposed decision, before anything leaves Autune.

    The classifier proposes about twenty decisions for a team meeting and two
    thirds of them are wrong (dummy team meetings, 2026-09-17), so nothing goes to
    Notion or Slack until somebody confirms it (#246). This is where that answer
    lives.

    **Keyed by the ``dec_`` id, with a real foreign key to ``ext_decisions``
    (#297).** A rerun no longer deletes and rebuilds every decision -- the id is
    derived from the meeting and the source utterances (#193), so
    ``service.build_decisions`` updates the row that id already names and only
    inserts or deletes where the set of ids actually changed. A decision whose
    sources are unchanged is the same row across a rebuild, so its review is
    never at risk of the foreign key; one whose sources changed is a different
    decision, and ``build_decisions`` deletes its review along with it rather
    than diffing the whole meeting's ids against a "kept" list to find it. The
    foreign key holds anyway, as a backstop against any other path that deletes
    a decision without going through there -- SQLite does not enforce it
    without being asked, which is why the delete is not left to it alone. The
    meeting foreign key still takes every row when the meeting goes.

    ``statement`` is the person's rewording, empty when they kept the model's. It
    is typed by a user, like an action item's edited description, so it is not
    masked on the way in; ``check_outbound`` reads it on the way out.

    **There is no reviewer column.** Who confirmed or rejected which decision is a
    record of one person's conduct in a meeting -- the shape ADR 0003 refuses, and
    the reason ``ext_confirmations`` and ``ext_edit_events`` have none either.
    Whether *anyone* may review is a permission (#246 point 1, #237), not a row.
    """

    __tablename__ = "ext_decision_reviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','confirmed','rejected')", name="ck_ext_decision_reviews_status"
        ),
    )

    decision_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ext_decisions.id", ondelete="CASCADE"), primary_key=True
    )
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    statement: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ExtClassification(Base):
    """What the classifier said one utterance is. Step 1 of the pipeline.

    Keyed by ``utterance_id``: one utterance has one answer, and a meeting that
    is reprocessed replaces its rows rather than adding a second set -- the
    idempotency ``async-pipeline.md`` asks for, held by the primary key rather
    than by care.

    **Only the five kinds are stored.** An utterance the model calls ``none``
    has no row, which is also how the contract says it: it is absent from
    ``ExtractionResult.classifications`` (#149). Storing it would be most of a
    meeting's utterances again, as rows that say nothing happened in them.

    ``model_version`` is on every row because a classification that cannot be
    attributed to a checkpoint cannot be compared against the next one, and a
    meeting processed before a retrain keeps the labels the old model gave it
    until it is processed again.
    """

    __tablename__ = "ext_classifications"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('commitment','decision','open_question','concern','ambiguous')",
            name="ck_ext_classifications_kind",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_ext_classifications_confidence"
        ),
    )

    utterance_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("utterances.id", ondelete="CASCADE"), primary_key=True
    )
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)
    nli_verified: Mapped[bool] = mapped_column(nullable=False, default=False)
    """Step 4 has not been built. False until it is, which is also what the
    contract's ``Classification.nli_verified`` defaults to."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def _utc(moment: datetime) -> datetime:
    """Read a stored timestamp as UTC when it comes back without a zone.

    The column is ``TIMESTAMPTZ`` and PostgreSQL returns it aware, but SQLite has
    no timezone type and hands back a naive value. Subtracting the two raises,
    and it would raise on the read path that decides whether a question has gone
    undecided -- the one place this must not fail.

    Reading it as UTC is not a guess: ``autune_core`` declares every timestamp
    with ``timezone=True`` and the worker runs on ``enable_utc``, so a naive
    value out of a store can only have been UTC going in.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


class ExtConfirmation(Base, TimestampMixin):
    """One ambiguous agreement, and the question to its speaker about it.

    The pipeline writes a row for every utterance it calls ``ambiguous``, before
    any DM goes out; ``sent_at`` is filled when one does. Until then the row is
    *not asked*, and ``AmbiguousAgreement.confirmation_sent`` is false -- the
    state the contract kept that flag for. Today every row is in it: the DM
    needs the speaker's Slack account, and nothing maps a user to one yet (#70),
    nor builds a team's Slack client (#30).

    Keyed by ``utterance_id`` rather than an id of its own: one utterance gets
    one question. Slack retries a button click it has not heard back from within
    three seconds, so the second click has to land on the same row instead of
    creating a second one.

    **No outcome is stored, only the two timestamps it is derived from.** A
    ``status`` column and a clock can disagree, and the one that would be wrong
    is the column — nothing runs at the deadline to update it. Deriving the
    answer on read means the 24-hour rule holds whether or not a periodic job is
    alive.

    **There is no responder column.** The DM goes to one person and comes back
    from that person, so storing who answered would record the speaker's own
    conduct about their own speech — the shape ADR 0003 refuses. Checking that a
    click came from the speaker would need a Slack account mapped to a user, and
    without ``users:read.email`` there is no directory to do it against (#70);
    the DM being one-to-one is what stands in for the check until then.
    """

    __tablename__ = "ext_confirmations"
    __table_args__ = (
        CheckConstraint(
            "resolved_kind IS NULL OR resolved_kind IN ('commitment','decision','concern')",
            name="ck_ext_confirmations_resolved_kind",
        ),
        CheckConstraint(
            "(resolved_kind IS NULL) = (responded_at IS NULL)",
            name="ck_ext_confirmations_answer_is_whole",
        ),
        CheckConstraint(
            "resolved_kind IS NULL OR sent_at IS NOT NULL",
            name="ck_ext_confirmations_answer_needs_a_question",
        ),
    )

    utterance_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("utterances.id", ondelete="CASCADE"), primary_key=True
    )
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    """Why the agreement was called ambiguous. Reaches E as
    ``AmbiguousAgreement.reason``; today always ``WEAK_ASSENT``."""

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the question reached the speaker; empty until it has. The deadline
    counts from here, not from when the row was created, so a retried send does
    not restart it and a row recorded days before its DM does not arrive already
    expired."""

    resolved_kind: Mapped[str | None] = mapped_column(String(32))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def outcome_at(self, now: datetime | None = None) -> str:
        """``resolved``, ``undecided``, ``pending`` or ``not_asked`` as of ``now``.

        An answer counts whenever it arrives. A late click is still the speaker
        telling us what they meant, and preferring a stale ``undecided`` over it
        would be choosing the clock over the person.

        A question never asked cannot time out. Calling it ``undecided`` would
        report the speaker as not having answered something nobody put to them.
        """
        if self.resolved_kind is not None:
            return RESOLVED
        if self.sent_at is None:
            return NOT_ASKED
        moment = now or datetime.now(UTC)
        return UNDECIDED if moment - _utc(self.sent_at) >= CONFIRMATION_TIMEOUT else PENDING

    @property
    def confirmation_sent(self) -> bool:
        """Whether the DM went out, named for the contract field."""
        return self.sent_at is not None


class ExtEditEvent(Base):
    """One correction, counted and not attributed.

    Edit cost is the product metric ADR 0006 defines: the share of items accepted
    with no edit, and how many edits it takes to reach a list the user accepts.

    **There is no user column here and there must not be one.** ADR 0003 forbids
    per-person metrics, and "who corrected the model most" is the same shape of
    data as a speaking ratio — it describes one person's conduct in a meeting.
    The metric only ever asks *how many*, so the row only carries *that a
    correction happened*.

    ``action_item_id`` is nullable rather than absent: a deletion event outlives
    the row it refers to, and the link is what goes, not the count.
    """

    __tablename__ = "ext_edit_events"
    __table_args__ = (
        CheckConstraint("kind IN ('created','deleted','edited')", name="ck_ext_edit_events_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_item_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("ext_action_items.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
