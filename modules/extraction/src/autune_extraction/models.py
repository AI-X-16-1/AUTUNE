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
    false,
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


PROJECT = "prj"
"""The id prefix of ``ext_projects`` rows -- B's own, not a shared entity's."""


class ExtProject(Base):
    """A project a team works on, as its members name it (the user, 2026-10-04).

    A team holds several projects and one meeting can talk about more than one;
    the team lists them here so the meeting's decisions and items can be told
    apart by project and sent out project by project. ``aliases`` are the other
    names people say for it, one per line ("오튠", "Autune"), matched in what was
    said (``projects.assign``). ``jira_project_key`` sends a project's issues to
    its own Jira project instead of the team's one, when set.

    Typed by a team member, not derived from speech: a name and some words. It
    goes with the team.
    """

    __tablename__ = "ext_projects"
    __table_args__ = (UniqueConstraint("team_id", "name", name="uq_ext_projects_team_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id(PROJECT))
    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    aliases: Mapped[str] = mapped_column(Text, nullable=False, default="")
    jira_project_key: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ExtProjectSend(Base):
    """Where a project's minutes for one meeting were sent, per tool (2026-10-04).

    One row per (meeting, project, target): the Notion page id, the Slack
    message as ``channel:ts``, or the Jira issue key. Sending again updates that
    copy instead of making a second one. Holds addresses, no text; goes with the
    meeting, and with the project -- each copied first to
    ``ext_project_send_cleanup`` so the copy outside goes too.

    ``external_id`` is empty only inside the transaction that claimed the row
    to make the first copy: two people sending at once make one copy.

    ``content_digest`` is ``service.source_digest`` over the minutes the copy
    last received -- a hash, not the text. A refresh compares it with the
    minutes as they are now and leaves a copy that already says them alone, so
    refreshing is safe to do on every change and to repeat after a failure;
    a copy whose write failed keeps its old digest and is still found stale
    by the next one (#787 review).
    """

    __tablename__ = "ext_project_sends"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ext_projects.id", ondelete="CASCADE"), primary_key=True
    )
    target: Mapped[str] = mapped_column(String(16), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_digest: Mapped[str | None] = mapped_column(String(64))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ExtMinutesEvent(Base):
    """A project's minutes as an all-day event on the meeting's day, in the
    calendar of the person who sent them (2026-10-04).

    Their own calendar, by their own click: team work is not copied into
    anybody else's (``calendar_sync``). Kept so sending again updates the same
    event, and so the event goes when the meeting expires or the person's
    account is deleted (``tasks.queue_meeting_calendar_events``,
    ``tasks.forget_user_calendar_events``) -- and when the project is deleted
    (``projects.delete_project``) -- and the event follows what changes after
    it was sent (``project_send.refresh``). The event id only, no text.

    ``event_id`` is empty only inside the transaction that claimed the row to
    make the event, so a double click makes one event.

    **A calendar disconnected before the event goes cannot be reached.** The
    grant is the only way into a person's calendar, so their minutes events
    stay there -- in their own calendar, put there by their own click, where
    they can delete them -- and the cleanup row is dropped with a log line
    after its tries (``tasks.drain_calendar_cleanup``). Removing them at
    disconnect needs a hook core does not have yet.
    """

    __tablename__ = "ext_minutes_events"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ext_projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_digest: Mapped[str | None] = mapped_column(String(64))
    """As ``ExtProjectSend.content_digest``: a hash of the minutes the event
    last received, so a refresh leaves an event alone that already says them
    and asks for its owner's grant only when there is something to write."""
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ExtProjectRefreshOwed(Base):
    """A meeting whose project minutes outside still have to be brought in line
    with what is confirmed now (#787 review).

    A refresh happens after the change it follows is committed, and talks to
    tools that can be down or disconnected. Without this a failed one was only
    logged: a sentence its speaker had deleted stayed in the team's Slack,
    Notion or Jira until the meeting's retention ran out. A refresh that leaves
    any copy behind writes the meeting here, deleted speech writes it in the
    transaction that drops the words, and
    ``tasks.retry_project_minutes_refresh`` tries again until every copy is in
    line. An id and a count, nothing said; goes with the meeting, whose copies
    are then retracted through ``ext_project_send_cleanup`` instead.
    """

    __tablename__ = "ext_project_refresh_owed"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExtProjectSendCleanup(Base):
    """A copy of a project's minutes still to take out of a team's tool, after
    the meeting or the project it belonged to was deleted (#787 review).

    ``ext_project_sends`` goes with the meeting and with the project, so before
    either goes its rows are copied here, and ``tasks.drain_project_send_cleanup``
    retracts each with the team's own connection: the Notion page emptied and
    trashed, the Slack message deleted, the Jira task emptied and closed.
    Addresses only, no text. No meeting or project key: the row has to outlive
    both. ``team_id`` cascades -- with the team goes every connection that
    could reach the copy.
    """

    __tablename__ = "ext_project_send_cleanup"
    __table_args__ = (
        UniqueConstraint("team_id", "target", "external_id", name="uq_ext_project_send_cleanup"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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
        CheckConstraint(
            "origin IN ('model','user','chat','followup')", name="ck_ext_action_items_origin"
        ),
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
    """Who made the item: ``model`` (the pipeline's draft), ``user`` (a person
    typed it -- edit cost counts it as one the model missed), or one of the
    agent layer's: ``followup`` (the Follow-up subagent's "후속 회의 잡기",
    #561) and ``chat`` (drafted from an utterance in the chat). A rerun replaces
    only ``model`` rows."""

    source_digest: Mapped[str | None] = mapped_column(String(64))
    """sha256 of the masked text of the utterances this was drawn from, in source
    order (``service.source_digest``) -- how a corrected transcript is noticed
    (#586). ``NULL`` until a run records it."""

    needs_recheck: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    """A line this came from was corrected after it was made, and what a person
    sees may still carry what was corrected: a summary rewritten from the new
    line, or their own wording (#586). Cleared when a person edits or reviews."""

    project_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("ext_projects.id", ondelete="SET NULL"), index=True
    )
    """Which of the team's projects this is about (``ext_projects``), or ``NULL``
    for none found. Set by ``projects.assign`` from what was said, or by a
    person; a deleted project leaves the row unassigned."""

    project_by_person: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    """A person chose ``project_id``: the rules never change it again."""

    description_resolved: Mapped[bool] = mapped_column(nullable=False, default=False)
    """True when ``description`` is ``ReferenceResolver``'s rewrite rather than
    the source utterance verbatim (#175, #366).

    Set once, in ``build_action_items``, by comparing the description actually
    stored against the utterance's own text -- not by trusting the resolver's
    own report, since a resolver that failed every check already returned the
    raw quote and this should read ``False`` for it the same as for a
    ``fake``-resolved or hand-added item. S18 shows this so a reviewer knows
    which descriptions are the speaker's own words and which are a model's
    paraphrase of them, worth a closer look given #366's own review found the
    paraphrase wrong often enough to matter.
    """

    related: Mapped[list[ExtActionItemRelated]] = relationship(
        back_populates="action_item",
        cascade="all, delete-orphan",
        # The database deletes them with the item (ondelete=CASCADE); the ORM need
        # not load them first just to delete them.
        passive_deletes=True,
        order_by="ExtActionItemRelated.id",
    )

    sources: Mapped[list[ExtActionItemSource]] = relationship(
        back_populates="action_item",
        cascade="all, delete-orphan",
        # Insertion order, which is the order they were handed to us. Ordering
        # here rather than at each read keeps a caller from sorting on ``id``
        # before the rows are flushed, when every id is still None.
        order_by="ExtActionItemSource.id",
    )


_EXCERPT_CHECK = (
    "(excerpt_start IS NULL AND excerpt_end IS NULL) "
    "OR (excerpt_start >= 0 AND excerpt_end > excerpt_start)"
)
"""Both offsets or neither, and a part that has something in it."""


class ExtActionItemSource(Base):
    """Which utterances an item came from.

    A table rather than a JSONB list because the drawer joins these back to read
    the quotation, and data-model.md rules JSONB out for anything you join on.

    ADR 0006 makes these load-bearing: they are how a user checks an item without
    replaying the meeting.

    A row outlives its utterance (ADR 0007, "Missing attribution is shown, not
    hidden"): deleting the utterance sets ``utterance_id`` to NULL instead of
    taking the row. The words go; the fact that the item had a source stays, so
    a model item whose transcript was deleted is not mistaken for a hand-added
    one. Every reader skips the NULLs for ids and counts them as
    ``deleted_source_count``.
    """

    __tablename__ = "ext_action_item_sources"
    __table_args__ = (
        UniqueConstraint("action_item_id", "utterance_id", name="uq_ext_action_item_sources"),
        CheckConstraint(_EXCERPT_CHECK, name="ck_ext_action_item_sources_excerpt"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_item_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ext_action_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    utterance_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "utterances.id", ondelete="SET NULL", name="fk_ext_action_item_sources_utterance_id"
        ),
        index=True,
    )
    """NULL once the utterance is deleted. Never written NULL by this module."""

    excerpt_start: Mapped[int | None] = mapped_column(Integer)
    excerpt_end: Mapped[int | None] = mapped_column(Integer)
    """Which part of the utterance the item was made from, as two offsets into
    its stored text -- no words (``excerpt``). Both NULL for the whole utterance,
    for a row from before these columns, and once the utterance was corrected."""

    action_item: Mapped[ExtActionItem] = relationship(back_populates="sources")


class ExtActionItemRelated(Base):
    """The other lines of the meeting an item's summary was written from.

    ``ext_action_item_sources`` is the utterance a commitment was said in; this is
    what the model said it drew on besides -- a turn earlier that names the thing,
    a line elsewhere about the same subject (``LlmResolver``, ``Resolution.used``).
    The drawer shows them beneath the summary so a person can check the sentence
    against what it was made from and correct it.

    **A table of its own, not more rows in ``ext_action_item_sources``.** Sources
    are what an item *is* -- ``ActionItem.source_utterance_ids`` reaches D and E,
    and a count of them means something there. These are what a summary
    *consulted*, decided by a model, and mixing the two would change what D and E
    read without anyone deciding it.

    Deleting an utterance deletes its row here: unlike a source, a consulted line
    that is gone leaves nothing worth keeping. Only consenting speakers' lines are
    ever written, and the reader filters on consent again.
    """

    __tablename__ = "ext_action_item_related"
    __table_args__ = (
        UniqueConstraint("action_item_id", "utterance_id", name="uq_ext_action_item_related"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_item_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ext_action_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    utterance_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("utterances.id", ondelete="CASCADE", name="fk_ext_action_item_related_utt"),
        nullable=False,
        index=True,
    )

    action_item: Mapped[ExtActionItem] = relationship(back_populates="related")


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
    site: Mapped[str | None] = mapped_column(String(64))
    """For Jira, the cloud id of the site ``external_id`` lives on. An issue key
    like ``KAN-1`` is unique only within a site; after a reconnect to another
    site, a key without its site would name someone else's issue (#458)."""
    synced_category: Mapped[str | None] = mapped_column(String(16))
    """For Jira, the status category (``new``, ``indeterminate``, ``done``) the
    issue was last left in by Autune or last read back from. It is how the
    read-back tells a person's move in Jira from a board edit that has not
    reached Jira yet (``jira_sync.read_back``). ``None`` until there
    is a baseline: a ref written before the read-back existed, or one whose
    issue never took the board's status; the next read-back records Jira's."""
    pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """For Jira, when the read-back last read the issue. The read-back reads the
    least recently read first, so a team with more issues than one run reads is
    read over several runs. ``None`` for an issue never read."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExtDecisionRef(Base):
    """The page -- or Jira issue -- a confirmed decision became in an outside
    tool, once.

    The same rule as ``ExtExternalRef`` for action items: keyed by the decision and
    the system, claimed before the call, filled in after it. A separate table
    rather than a second key on that one, kept without a foreign key to
    ``ext_decisions`` even though ``build_decisions`` no longer deletes and
    rebuilds every row on a rerun (#297) -- a decision whose id genuinely goes
    away has a claim with no page deleted here by name, the same explicit way
    as its sources and its review. **A row that names a page outlives its
    decision** (#669): it is how the page is found and retired, after the
    decision stopped being confirmed, was deleted, or was dropped by a rerun;
    retired, the row stays with no page. Keyed by the ``dec_`` id like
    ``ext_decision_reviews``, and taken with the meeting.
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
    site: Mapped[str | None] = mapped_column(String(64))
    """For a Jira issue, the cloud id of the site it is on: a key is unique only
    within a site, as for ``ExtExternalRef.site``. ``None`` for a Notion page."""
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
    """The decision as settled, in one sentence, as a person sees it and as it
    leaves: the noun-ended line, or a model's summary of it (``original_statement``
    is what this was made from). PII-masked like every utterance it is drawn from
    — there is no unmasked text to reach this column."""

    original_statement: Mapped[str | None] = mapped_column(Text)
    """The same sentence before it was tidied or summarised -- the substance turn
    as said, with the owner and deadline. This is what module D is sent, because D
    embeds and compares statements against a threshold tuned on this shape and a
    rewrite made for the screen must not move it. ``NULL`` for a decision a person
    typed and for rows from before this column: read it as
    ``original_statement or statement``."""

    statement_resolved: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    """True when ``statement`` is a model's sentence about the decision -- the
    classifier's one line or the resolver's write-up -- rather than the settling
    line tidied. ``ExtActionItem.description_resolved`` for a decision.

    Set in ``build_decisions``, where the sentence is chosen, by whether a
    summary replaced the line. It is what ``forget_speech`` reads when the
    speaker deletes the speech: a model's sentence is the team's record and
    stays, the line itself goes (the user, 2026-10-06, on #894). Before this
    column that was read off ``ext_decision_related`` -- whether the sentence
    cited a line -- which was the same thing only while every write-up cited
    one."""

    source_digest: Mapped[str | None] = mapped_column(String(64))
    """sha256 of the masked text of the utterances this was drawn from, in source
    order (``service.source_digest``) -- how a corrected transcript is noticed
    (#586). ``NULL`` until a run records it."""

    needs_recheck: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    """A line this came from was corrected after it was made, and what a person
    sees may still carry what was corrected: a summary rewritten from the new
    line, or their own wording (#586). Cleared when a person edits or reviews."""

    project_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("ext_projects.id", ondelete="SET NULL"), index=True
    )
    """Which of the team's projects this is about (``ext_projects``), or ``NULL``
    for none found. Set by ``projects.assign`` from what was said, or by a
    person; a deleted project leaves the row unassigned."""

    project_by_person: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    """A person chose ``project_id``: the rules never change it again."""

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
    related: Mapped[list[ExtDecisionRelated]] = relationship(
        back_populates="decision",
        cascade="all, delete-orphan",
        # Deleted with the decision by the database (ondelete=CASCADE); the ORM
        # need not load them first, and the unit suite that lists its tables by
        # hand need not know this one exists.
        passive_deletes=True,
        order_by="ExtDecisionRelated.id",
    )


class ExtDecisionRelated(Base):
    """The other lines of the meeting a decision's summary says it was written from.

    What ``ext_action_item_related`` is for an item: the lines the model said it
    drew on, shown beneath the summary so a person can check the sentence and
    correct it. Not more rows in ``ext_decision_sources`` -- those are the
    utterances the decision was settled in, D keys its ``dec_`` id on them, and a
    consulted line must never change that id.
    """

    __tablename__ = "ext_decision_related"
    __table_args__ = (
        UniqueConstraint("decision_id", "utterance_id", name="uq_ext_decision_related"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ext_decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    utterance_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("utterances.id", ondelete="CASCADE", name="fk_ext_decision_related_utt"),
        nullable=False,
        index=True,
    )

    decision: Mapped[ExtDecision] = relationship(back_populates="related")


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
        CheckConstraint(_EXCERPT_CHECK, name="ck_ext_decision_sources_excerpt"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ext_decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    utterance_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("utterances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    excerpt_start: Mapped[int | None] = mapped_column(Integer)
    excerpt_end: Mapped[int | None] = mapped_column(Integer)
    """``ExtActionItemSource.excerpt_start`` and ``excerpt_end`` for a decision."""

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


class ExtWeeklyDigest(Base):
    """That a person was sent the weekly digest of their open items for one
    week, through one team's Slack (the user, 2026-10-04). The primary key is
    the "once", as ``ext_due_reminders``'s is. No text: the message is not
    kept. Goes with the person and with the team."""

    __tablename__ = "ext_weekly_digests"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    week_start: Mapped[date] = mapped_column(Date, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExtDailyDigest(Base):
    """That a person was sent the morning DM for one day, through one team's
    Slack (the user, 2026-10-05). The primary key is the "once", as
    ``ext_weekly_digests``'s is, and ``sent_at`` of the latest row is where the
    next one's "since the last one" starts. No text: the message is not kept.
    Goes with the person and with the team."""

    __tablename__ = "ext_daily_digests"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExtWorkReport(Base):
    """That a person was sent the work-report draft for one day,
    through one team's Slack (``work_report``, the user 2026-10-07). The
    primary key is the "once", as ``ext_daily_digests``'s is. No text: the
    message is not kept. Its own table because that one's key is the same
    three columns and its latest row is where the next morning DM counts
    from.

    **A row lives for its day only.** The draft goes only on a day something
    of the person's was finished or moved, so a row kept would say which days
    a person worked (ADR 0003; mkkim68, review of #954): the sending task
    deletes every earlier day's row each time it runs
    (``work_report.forget_past_days``). Goes with the person and with the
    team before that."""

    __tablename__ = "ext_work_reports"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExtMeetingNotice(Base):
    """That a person was told, right after a meeting, that work of it had
    landed on them (the user, 2026-10-07; ``meeting_notice``). The primary key
    is the "once": one notice a person and meeting. No text and no count: the
    message is not kept.

    **Sent or refused.** A notice the outbound check refused keeps its row too
    (``meeting_notice.settle_refused_notice``), so that it is reported once
    and not built again; nothing on the row tells the two apart, and
    ``sent_at`` is then when it was refused. Goes with the meeting -- its
    retention expiry included -- and with the person."""

    __tablename__ = "ext_meeting_notices"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExtNotificationPause(Base):
    """The days a person asked not to get the morning DM or Monday's digest --
    leave, as they set it themselves (the user, 2026-10-05).

    One range a person, replaced when they set another and deleted when they
    clear it. **It says when a person is away, so it is theirs alone**: only
    they can read or write it (the routes name nobody), no screen shows it to
    a teammate, and nothing derives anything else from it -- it only stops a
    message. It is not kept past its last day: the morning run deletes a range
    that has ended (``service.forget_ended_pauses``). The due-date reminders
    do not read it; a deadline is not put off by leave. Goes with the account.

    **The one way the dates leave Autune is the person's own tick**
    (``leave_calendar``, the user 2026-10-06): "내 Google 캘린더에도 추가" puts
    one private all-day event over the range on their own calendar, through
    their own grant. ``calendar_event_id`` is that event, kept so a changed
    range moves it and a cleared one removes it; ``None`` for a pause nobody
    asked to have on a calendar. The id goes with the row, and the event then
    stays on the calendar as the person's own.

    ``calendar_claimed_at`` is when a save of this person's went to the
    calendar and has not come back: Google is asked with no transaction open,
    so this, not the row's lock, is what keeps a second save out meanwhile
    (``leave_calendar.CLAIM_FOR``). ``None`` at rest. It says nothing a
    reader could use -- no screen, route or log carries it.
    """

    __tablename__ = "ext_notification_pauses"
    __table_args__ = (
        CheckConstraint("ends_on >= starts_on", name="ck_ext_notification_pauses_order"),
    )

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calendar_event_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    calendar_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ExtPublicHoliday(Base):
    """A public holiday in Korea, as Google's public holiday calendar listed it
    at the last read (``days_off.py``, the user 2026-10-05). No digest goes on
    one. Dates of public record: nothing here is about a person, a team or a
    meeting. Replaced whole on every read; ``read_at`` is that read's time,
    and a table with none newer than ``days_off.FRESH_FOR`` is not used."""

    __tablename__ = "ext_public_holidays"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExtDueReminder(Base):
    """That an item's assignee was sent a due-date reminder of one kind for one
    due date (``reminders``). The primary key is the "once": a second run, a
    redelivered task or two workers find the row and send nothing.

    No text and no person: the message is not kept, and who it went to is
    the item's assignee at the time, which the item already says. Goes with
    the item, and so with its meeting.

    A row also stands for a reminder the outbound check refused: it is
    settled, reported once, and not tried again (review of #751)."""

    __tablename__ = "ext_due_reminders"
    __table_args__ = (
        CheckConstraint("kind IN ('due_soon','overdue')", name="ck_ext_due_reminders_kind"),
    )

    action_item_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ext_action_items.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    due_date: Mapped[date] = mapped_column(Date, primary_key=True)
    """The date the reminder was about. A due date moved later is a new date,
    and the item is owed a reminder for it."""
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """When the reminder was settled: sent, or, for a refusal, when the outbound
    check refused it. Named for the common case."""


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
    answer on read means the 72-hour rule holds whether or not a periodic job is
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

    dm_channel: Mapped[str | None] = mapped_column(String(32))
    dm_ts: Mapped[str | None] = mapped_column(String(32))
    """Where the DM landed -- Slack's ``D...`` conversation and the message's
    ``ts`` -- so a quotation masked again after a PII report can be corrected in
    place (#586). Empty for a DM sent before this was kept: that one cannot be."""

    dm_digest: Mapped[str | None] = mapped_column(String(64))
    """``service.source_digest`` of the line the DM quotes, to tell that it changed."""

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

    ``fields`` is which fields an edit changed -- names, never values (#109).
    The drawer's history (S18) shows "기한 수정됨"; keeping the value before an
    edit would keep the sentence a person chose to replace, a tombstone by
    another name (privacy.md section 4).

    ``closed`` is the one kind that is not a correction: the item was **closed
    without being finished** (#856, the user 2026-10-07). An item has no
    cancelled state, so a close leaves it ``done``, and "what a person
    finished" is read from the status and these rows alone -- this kind is
    what tells a close from finished work (``service.closed_unfinished``). It
    says that and when, about an item; like every row here it does not say
    who. It carries no ``fields``: a reader that looks for an edit of the
    status must not find one in it. Edit cost does not count it.
    """

    __tablename__ = "ext_edit_events"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('created','deleted','edited','closed')", name="ck_ext_edit_events_kind"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_item_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("ext_action_items.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    fields: Mapped[str | None] = mapped_column(String(200))
    """Comma-separated names of the fields an ``edited`` event changed, sorted;
    ``None`` for the other kinds and for rows written before #109."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExtSyncFailure(Base):
    """That the last attempt to copy an item to one outside system failed, what
    kind of failure it was, and when (#680).

    The claim in ``ext_external_refs`` is rolled back when a send fails, so a
    failure left no trace and the board could only say "sent" or "sending".
    This row is the trace. **A kind and a time, nothing else**: not the
    outside service's message, which may echo what was sent, and not what
    was being sent. One row per item and system -- the latest failure --
    removed by the next attempt that succeeds, and gone with the item.
    """

    __tablename__ = "ext_sync_failures"
    __table_args__ = (
        CheckConstraint(
            "system IN ('notion','jira','calendar')", name="ck_ext_sync_failures_system"
        ),
        CheckConstraint(
            "kind IN ('privacy','reconnect','unreachable','rejected')",
            name="ck_ext_sync_failures_kind",
        ),
    )

    action_item_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ext_action_items.id", ondelete="CASCADE"), primary_key=True
    )
    system: Mapped[str] = mapped_column(String(16), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExtSyncRetry(Base):
    """When a person last pressed "다시 시도" for an item (#680, lsh2217's review
    of #754). Each press runs Notion, the calendar and Jira once more, so a
    second press inside ``sync_state.RETRY_COOLDOWN`` is refused. One time per
    item, nothing else; gone with the item."""

    __tablename__ = "ext_sync_retries"

    action_item_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ext_action_items.id", ondelete="CASCADE"), primary_key=True
    )
    retried_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExtCalendarEvent(Base):
    """The all-day event a confirmed item's due date became on its assignee's
    own Google Calendar (#435).

    One per item: the primary key is the item. ``user_id`` is whose calendar
    holds it, so reassigning the item moves the event from one person's
    calendar to the other's. ``synced_due_date`` is the date Autune last wrote
    or read there -- what the read-back compares against, so a date Autune
    wrote itself is never mistaken for one the person moved.

    ``event_id`` is empty only inside the transaction that claimed the row and
    is creating the event, the way ``ext_external_refs`` is claimed before its
    Notion page exists.

    Deleting the item, the meeting or the user deletes the row. The event stays
    on the person's calendar: Autune does not reach into a calendar to tidy up
    after a deletion it was not asked to make there.
    """

    __tablename__ = "ext_calendar_events"

    action_item_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ext_action_items.id", ondelete="CASCADE"), primary_key=True
    )
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[str | None] = mapped_column(String(1024))
    synced_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExtCalendarCleanup(Base):
    """A due-date event still to take off a person's calendar, after the meeting
    its item belonged to was deleted (#588).

    ``ext_calendar_events`` goes with the meeting, so the meeting hook copies
    the event here first and ``tasks.drain_calendar_cleanup`` removes it with
    that person's own grant. No meeting key: the row has to outlive the meeting.
    A leave event Google did not let go of when its dates were cleared waits
    here too (``leave_calendar``).
    ``user_id`` cascades -- an account deletion removes its own events in its
    hook, and nothing could remove them after.
    """

    __tablename__ = "ext_calendar_cleanup"
    __table_args__ = (UniqueConstraint("user_id", "event_id", name="uq_ext_calendar_cleanup"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_ext_calendar_cleanup_user"),
        nullable=False,
        index=True,
    )
    event_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExtDueReminderOptOut(Base):
    """A person who turned the due-date reminders off for themselves (review
    of #751) -- and with them Monday's digest of their own open items (#792):
    one switch for Autune's DMs about a person's items. On unless they did:
    a row means off, and turning them back on deletes it. Only the person,
    never which items or teams; goes with the account."""

    __tablename__ = "ext_due_reminder_optouts"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExtExternalCleanup(Base):
    """A deleted item's Notion page or Jira issue still owed its cleanup (#692).

    Deleting an item trashes its page and closes its issue in the deleting
    request, best effort (``tasks.trash_notion_page``, ``tasks.close_jira_issue``).
    When that call cannot get through, the item and its ``ext_external_refs``
    row go regardless, so the request records here what it could not do and
    ``tasks.drain_external_cleanup`` retries it. Ids only -- never what the item
    said. Keyed to the team, which cascades: a deleted team has nothing left to
    reach the page with.
    """

    __tablename__ = "ext_external_cleanup"
    __table_args__ = (
        UniqueConstraint("team_id", "system", "external_id", name="uq_ext_external_cleanup"),
        CheckConstraint("system IN ('notion','jira')", name="ck_ext_external_cleanup_system"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("teams.id", ondelete="CASCADE", name="fk_ext_external_cleanup_team"),
        nullable=False,
        index=True,
    )
    system: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    site: Mapped[str | None] = mapped_column(String(64))
    """Jira's cloud id the key is from; a key on another site is someone else's issue."""
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExtCalendarPoll(Base):
    """When B last read a person's calendar back (#435) -- the ``updatedMin`` of
    the next read. B's own sync state, kept here rather than in
    ``user_integrations``, which modules read and never write."""

    __tablename__ = "ext_calendar_polls"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    polled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExtNotionTarget(Base):
    """Where a team's Notion sync writes: the page Autune's databases were made
    under and the three database ids (#428).

    B's own table rather than keys in ``team_integrations.config``, which is the
    settings layer's and which modules never write (data-model.md). A one-click
    connection stores the token there; B makes the databases and records them
    here. A team connected through the local dev page still has the ids in its
    config; ``notion_setup.database_id`` reads this table first and that second.

    Keyed by team, deleted with it.
    """

    __tablename__ = "ext_notion_targets"

    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    parent_page_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_db_id: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_db_id: Mapped[str] = mapped_column(String(64), nullable=False)
    minutes_db_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(64))
    """The Notion workspace these databases live in. A row naming another
    workspace than the team's current connection is ignored (#467 review)."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExtMeetingNote(Base):
    """The team's own memo on a meeting's summary tab (S15 요약, #421).

    Free text a member types, not anything a model derived: the summary tab's
    structure comes from B's rows, and this is the part a person writes. One
    per meeting, deleted with it; a blank memo is no row. No author column,
    the same rule as ``ext_edit_events`` -- the tab says what the team noted,
    not who noted it.
    """

    __tablename__ = "ext_meeting_notes"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ExtMeetingSummary(Base):
    """A meeting's summary written by a cloud model (#421 v2, ``summary_impl=llm``).

    Model output over the meeting's consented, masked lines, names put back --
    meeting content, shown to the team on the 요약 tab. ``source_digest`` is
    ``service.source_digest`` over the lines it was written from: a summary
    whose lines have changed since (a correction, a deletion, a change of
    consent) no longer matches what the meeting says and is not shown
    (``service.meeting_summary``); the next run writes a new one. Not showing
    it is not enough for words a person took back, so the row itself goes:
    deleted speech deletes it outright (``service.forget_speech``), and every
    extraction and every ``summarize_meeting`` deletes one whose lines have
    changed before anything else, whether or not a new one can be written
    (``service.drop_stale_summary``). One per meeting, deleted with it, so it
    keeps the meeting's retention.

    ``points`` holds one sentence per line: each was checked to be one line.
    """

    __tablename__ = "ext_meeting_summaries"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    overview: Mapped[str] = mapped_column(Text, nullable=False)
    points: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ExtForgottenUtterance(Base):
    """An utterance its speaker deleted, until module A has removed the row
    (#421 v2, review of #782).

    B's speech hook commits before A deletes the utterances
    (``tasks.forget_deleted_speech``), so for a moment a line B was told to
    forget is still in the shared table. ``service.summary_lines`` leaves out
    every utterance named here. A summary asked for in that moment is then not
    written from the deleted words, and one the model was still writing when
    the hook ran no longer matches the lines when it comes to be stored
    (``service.store_meeting_summary``).

    An id and a time, nothing that was said. The row goes with the utterance
    (CASCADE), which is when it stops being needed. If A's deletion fails the
    row stays and the line stays out of every summary: erring toward deleting
    more, as the hook itself does.
    """

    __tablename__ = "ext_forgotten_utterances"

    utterance_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("utterances.id", ondelete="CASCADE"), primary_key=True
    )
    forgotten_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExtExtractionRun(Base):
    """Which speech the last extraction of a meeting was allowed to read (#518).

    ``consent_key`` is a digest of the ids of the utterances whose speaker had
    consented when the meeting was last extracted. Consent can change after
    that -- today only by A's ``attest_consent`` (False to True, #190) -- and
    nothing announces it (#360), so ``tasks.reextract_consent_changes``
    compares this key with the consent as it is now and extracts again where
    they differ. A digest because a comparison is all it is for: it cannot be
    read back into which utterances, or whose, were in.

    One row per meeting, written in the same transaction as the extraction's
    rows, deleted with the meeting. A meeting with no row has not been
    extracted yet, or was extracted before this table existed; the sweep leaves
    it alone.
    """

    __tablename__ = "ext_extraction_runs"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    consent_key: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ExtExtractionAttempt(Base):
    """What became of a meeting's extraction since it last went through
    (``attempts``): how many times in a row it failed, and a person's request
    to run it again.

    ``failures`` counts runs that raised since the last one that did not;
    ``tasks.retry_failed_extractions`` tries again while it is under
    ``attempts.MAX_ATTEMPTS`` and tells the team's Slack channel once when it
    is not (``told_at``). ``reason`` is the class of the last error and nothing
    from it -- an exception over a meeting's rows can carry what was said.

    ``requested`` is the 액션 tab's "다시 추출", waiting for the worker: the
    API process has no broker to queue on, so the request is a row and
    ``tasks.run_requested_extractions`` takes it. ``requested_at`` stays after
    that, for the cooldown.

    One row per meeting, deleted with the meeting. Nothing here names a person.
    """

    __tablename__ = "ext_extraction_attempts"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    failures: Mapped[int] = mapped_column(nullable=False, default=0)
    reason: Mapped[str | None] = mapped_column(String(80))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    told_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested: Mapped[bool] = mapped_column(nullable=False, default=False)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
