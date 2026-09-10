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
from autune_core.ids import ACTION_ITEM, new_id

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
"""The three outcomes of a confirmation. Derived from timestamps, never stored
— see ``ExtConfirmation``."""


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
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="needs_confirmation")
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    """A hand-added item is 1.0: a person typing it is the certainty."""

    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="model")

    sources: Mapped[list[ExtActionItemSource]] = relationship(
        back_populates="action_item", cascade="all, delete-orphan"
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
    """One ambiguous agreement the speaker was asked about.

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

    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """When the question reached the speaker. The deadline counts from here, not
    from when the row was created, so a retried send does not restart it."""

    resolved_kind: Mapped[str | None] = mapped_column(String(32))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def outcome_at(self, now: datetime | None = None) -> str:
        """``resolved``, ``undecided`` or ``pending`` as of ``now``.

        An answer counts whenever it arrives. A late click is still the speaker
        telling us what they meant, and preferring a stale ``undecided`` over it
        would be choosing the clock over the person.
        """
        if self.resolved_kind is not None:
            return RESOLVED
        moment = now or datetime.now(UTC)
        return UNDECIDED if moment - _utc(self.sent_at) >= CONFIRMATION_TIMEOUT else PENDING

    @property
    def confirmation_sent(self) -> bool:
        """Always true for a stored row, and named for the contract field.

        A row exists only because a DM went out; ``AmbiguousAgreement`` still
        carries the flag because an ambiguity found with no DM sent is a state
        the contract has to be able to express.
        """
        return True


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
