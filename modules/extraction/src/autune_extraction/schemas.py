"""Internal schemas for module B.

Anything another module needs belongs in ``packages/contracts``, not here.
These are request and response bodies for this module's own HTTP surface, which
nobody else parses.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autune_contracts.enums import ActionStatus


class ActionItemCreate(BaseModel):
    """An item the model missed, typed by a person.

    No ``confidence``: a person typing an item is the certainty, and the service
    stores 1.0. Letting a caller set it would put a model score on a human
    judgement and quietly corrupt the metric that compares the two.
    """

    model_config = ConfigDict(extra="forbid")

    meeting_id: str = Field(pattern=r"^mtg_")
    description: str = Field(min_length=1, max_length=2000)
    assignee_id: str | None = Field(default=None, pattern=r"^user_")
    assignee_label: str | None = Field(default=None, max_length=200)
    due_date: date | None = None
    source_utterance_ids: list[str] = Field(default_factory=list)
    """Optional. A hand-added item often has no utterance behind it — that is
    what "the model missed it" means — and the drawer renders that state."""


class BulkActionItems(BaseModel):
    """Several items of the 확인 필요 column at once (the user, 2026-10-04)."""

    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1, max_length=100)
    action: Literal["confirm", "delete"]


class BulkActionResult(BaseModel):
    """Which items were confirmed or deleted, and which were left as they were:
    unknown, another team's, or no longer waiting for confirmation."""

    confirmed: list[str]
    deleted: list[str]
    skipped: list[str]


class ActionItemUpdate(BaseModel):
    """A correction to one item. Every field optional; absent means unchanged.

    ``meeting_id`` and ``origin`` are not here. An item does not move between
    meetings, and rewriting where it came from would erase the distinction edit
    cost is measured on.
    """

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, min_length=1, max_length=2000)
    assignee_id: str | None = Field(default=None, pattern=r"^user_")
    assignee_label: str | None = Field(default=None, max_length=200)
    due_date: date | None = None
    status: ActionStatus | None = None

    def changes(self) -> dict[str, object]:
        """Only the fields the caller actually sent.

        ``exclude_unset`` rather than ``exclude_none``: clearing a due date is a
        correction like any other, and the two are indistinguishable without it.
        """
        return self.model_dump(exclude_unset=True)


class ExternalRefRead(BaseModel):
    """Where one confirmed item or decision stands with one outside system.

    Not ``autune_contracts.extraction.ExternalRef``: that type is the outbound
    event to D and E, and it requires ``url`` because it is only ever built for
    a ref that finished. This is this module's own read, so it has to say the
    other two states a sync can be in -- ``url`` is ``None`` while the row is
    claimed but the call has not returned (in flight) or did not survive it
    (failed); no row at all means nothing has tried yet, and neither list nor
    drawer constructs one for that case.

    On the list, not gated behind the drawer the way ``sources`` is: a system
    name, a url and an id are not meeting content, so ``description`` and
    ``assignee_label``'s reasoning for being on the list already covers this.
    """

    system: Literal["notion", "jira"]
    """The systems ``ext_external_refs`` holds a row for -- the table's own
    check constraint. Jira was dropped once (#82) and this type narrowed to
    ``"notion"`` with it; #458 brought Jira's sync back and wrote
    ``system='jira'`` rows again while the type stayed narrow, so listing any
    item with a Jira issue raised (#650). A system added to the constraint is
    added here in the same change."""
    url: str | None
    external_id: str | None


class SyncFailureRead(BaseModel):
    """The last attempt to copy the item to ``system`` failed (#680).

    ``kind`` is all that is known and all that is kept: ``privacy`` -- the
    outbound check refused the text; ``reconnect`` -- the connection's grant
    was refused and a person has to connect again; ``unreachable`` -- the
    service timed out or answered with a server error; ``rejected`` -- it
    said no. No message from the service, and nothing of what was sent.
    """

    system: Literal["notion", "jira", "calendar"]
    kind: Literal["privacy", "reconnect", "unreachable", "rejected"]
    failed_at: datetime


class SyncLogFailure(BaseModel):
    """A copy of an item that failed and still stands, on S28's "동기화 기록".

    ``SyncFailureRead`` with the item and its meeting beside it, because the
    drawer has no card to hang the failure on. ``description`` is the item's
    text as the board shows it."""

    action_item_id: str
    meeting_id: str
    meeting_title: str
    description: str
    system: Literal["notion", "jira", "calendar"]
    kind: Literal["privacy", "reconnect", "unreachable", "rejected"]
    failed_at: datetime


class SyncLogCopy(BaseModel):
    """A copy of an item that was made: a Notion page, a Jira issue, or an
    event on the reader's own calendar. ``copied_at`` is when it was first
    made. ``url`` is the page or issue; a calendar event has none."""

    action_item_id: str
    meeting_id: str
    meeting_title: str
    description: str
    system: Literal["notion", "jira", "calendar"]
    url: str | None
    copied_at: datetime


class SyncLogRead(BaseModel):
    """What a team's copies outside Autune did lately (``sync_log``): the
    failures standing and the latest copies made, each newest first."""

    failures: list[SyncLogFailure]
    copies: list[SyncLogCopy]


class CalendarState(BaseModel):
    """Whether the item is on its assignee's calendar, and if not, why not.

    An item with no event is usually not a failure: it is not something a
    calendar event is made for. The board could not say which, and somebody
    who had added an item by hand was left asking why nothing appeared
    (2026-10-02). ``reason`` names the first thing missing.

    ``not_confirmed``, ``no_due_date``, ``no_account`` (the assignee is a
    typed name or nobody) and ``not_on_team`` are about the item and are sent
    to any reader. ``sent``, ``none`` with no reason, and ``not_connected``
    are about the assignee's own calendar -- each says whether that person
    has connected one -- and are sent **only when the reader is the
    assignee**. For anybody else the detail carries no ``calendar`` at all
    once the item itself lacks nothing."""

    state: Literal["sent", "none"]
    reason: (
        Literal["not_confirmed", "no_due_date", "no_account", "not_on_team", "not_connected"] | None
    ) = None


class ActionItemRead(BaseModel):
    """One item as this module's own screens read it.

    Not ``from_attributes``: two of these fields are not columns on the row.
    ``source_utterance_ids`` lives in the link table and ``is_candidate`` is
    derived from a setting, so an ``ExtActionItem`` alone cannot answer either.
    Built by ``service.read_model``.
    """

    id: str
    meeting_id: str
    meeting_title: str | None = None
    """The title of the meeting the item came from. The board across every
    meeting shows it on each card, so a person can tell which meeting an item
    belongs to without opening it (mentoring, 2026-10-01). Read with the item,
    never stored on it."""
    team_id: str | None = None
    """The team whose meeting the item came from (``meetings.team_id``), read
    with the item and never stored on it -- an item has no team of its own,
    only a meeting. The board across every meeting shows the items team by
    team with it (the user, 2026-10-06); the names are ``GET /teams/mine``."""
    description: str
    title: str | None = None
    """``description`` in twenty characters or fewer, for a card's top line
    (``ExtActionItem.title``). ``None`` when the item has none; the screen
    then cuts the description itself."""
    description_resolved: bool = False
    """Whether ``description`` is ``ReferenceResolver``'s rewrite rather than
    the source utterance verbatim (#175, #366). S18 shows this so a reviewer
    knows which descriptions are the speaker's own words and which are a
    model's paraphrase of them -- worth a closer look, given #366's own review
    found the paraphrase wrong often enough to matter. Always ``False`` for a
    hand-added item, a raw quote (no resolver configured), or a resolution
    that failed every check and fell back to the quote."""
    assignee_id: str | None
    assignee_label: str | None
    assignee_name: str | None = None
    """The assignee's current display name, read fresh from ``users`` -- never
    stored. ``assignee_label`` is "the name as spoken, kept when it does not
    resolve to an account" (``ExtActionItem.assignee_label``'s own docstring);
    an identified assignee has no label at all, so a card showing only
    ``assignee_label`` reads an assigned item as unassigned. This is the other
    half: set only when ``assignee_id`` resolves to an account that still
    exists, so a screen can show *somebody's name* without caring which half
    filled it in."""
    due_date: date | None
    due_text: str | None = None
    """The phrase the date was parsed from ("다음 주 화요일", "9/20"), kept
    beside the resolved ``due_date`` rather than replacing it on this response
    -- S18 shows both (ui-spec): the resolved date to act on, and the speaker's
    own words so a person can judge the parse rather than take it on faith.
    ``None`` for a hand-added item, or a model item where no date phrase was
    said at all; ``slots.parse_due`` leaves both fields empty rather than
    guessing one from the other."""
    status: str
    confidence: float
    origin: str
    carried_meetings: int = 0
    """For an open item: how many of its team's meetings have been held since
    it was made (``service.meetings_since``). ``STALE_AFTER`` or more reads as
    stuck on the board."""
    project_id: str | None = None
    """The team's project this item is about (``ext_projects``), or ``None``."""

    source_utterance_ids: list[str]
    """The utterances this item was drawn from. Empty for a hand-added item.

    This response did not carry them until now, and the gap was not cosmetic:
    S17's card reads the list to decide between "근거 발화 N건" and "직접 추가",
    and an absent list is an empty one. Every model-extracted item was therefore
    labelled as one somebody typed -- the distinction ADR 0006 and the edit-cost
    metric are built on, printed inverted, with nothing failing.

    Ordered by row id, which is insertion order. ``ext_action_item_sources`` has
    no position column; when the drawer needs them in spoken order that is the
    change to make, not a sort here over a field that does not exist.

    Only utterances that still exist. One that was deleted is counted in
    ``deleted_source_count`` instead.
    """

    deleted_source_count: int = 0
    """How many of this item's sources were deleted after it was made (ADR 0007,
    "Missing attribution is shown, not hidden").

    Without it, a model item whose transcript went has an empty
    ``source_utterance_ids`` -- the same shape as a hand-added item -- and the
    drawer printed "직접 추가한 항목" over it. ``origin`` says who made the
    item; this says its evidence is gone, and the screen needs both."""

    needs_recheck: bool = False
    """A line this was drawn from was corrected after it was made (a PII report,
    #586), and the text shown may still need a person's eye: a summary rewritten
    from the corrected line, or their own wording. Cleared by their next edit."""

    closed_unfinished: bool = False
    """A ``done`` item that was closed without being finished
    (``service.close_without_finishing``, #856): the card says 닫힘 so that
    the 완료 column does not show it as work somebody finished. ``False`` for
    every other status, and again for one re-opened and then finished. It is
    about the item; who closed it is not kept."""

    needs_reassignment: bool = False
    """An open item (``todo`` or ``in_progress``) whose assignee is no longer a
    member of the meeting's team (ADR 0007, "An open commitment is reassigned,
    never orphaned"). S17 puts it at the top of its column.

    Derived on every read from ``team_members``, the way module D filters
    ``key_stakeholders_absent``: nothing in the product removes a member yet,
    so there is no departure event to store it from. Not a member covers a
    guest who never was one as well as someone who left -- either way nobody
    on the team holds the item, which is what the flag is for.

    When the assignee is not a member, ``assignee_id`` and ``assignee_name`` on
    this response are ``None`` whatever the status: ADR 0007's "its assignee
    clears", applied at read time. The stored column keeps the id, so a person
    who rejoins gets their items back. A ``done`` item does not need
    reassigning and stays ``False``."""

    is_candidate: bool
    """Whether the model was unsure enough that this is shown apart from the
    board rather than asserted on it.

    Decided here rather than in the browser. The threshold is a property of the
    classifier, and a client comparing against a number it happens to hold is one
    deploy away from disagreeing with the server about which items the meeting
    produced.

    **False for everything while ``candidate_confidence`` is unset**, which is
    its default until #10 measures one. **False once a person has confirmed
    the item**, whatever its confidence -- confirming moves ``status``, not
    the model's score, so scoring only on confidence would keep a low-
    confidence item candidate forever, back on the review screen every visit
    after the one where it was already confirmed (#295).
    """

    sync_failures: list[SyncFailureRead] = Field(default_factory=list)
    """Systems whose last copy of this item failed (#680). A kind and a time;
    on the list for the reason ``sync_refs`` is -- it is not meeting content.
    Notion and Jira are the team's connections and their failures go to any
    reader; a ``calendar`` failure is one person's and is sent only to the
    item's assignee. Empty for an item nothing has failed for."""
    sync_refs: list[ExternalRefRead]
    """One entry per system this item has been claimed for -- today, at most
    ``notion`` (#30). ``jira`` was designed (ui-spec S18, S28) but dropped
    before being built (#82), so it never appears rather than being shown
    always-empty. Ordered by ``created_at``, which for one system is also
    insertion order.

    Not ``external_refs``: ``ActionItem`` (the contract this extends) already
    has a field by that name -- the outbound one, ``list[ExternalRef]``, which
    requires ``url`` -- and TypeScript's `extends` cannot narrow an optional,
    stricter-typed inherited field to this one, which also reports the
    in-flight and failed states. Same name collision, same fix, as
    ``ReviewDecision.sync_refs`` below would have hit if ``Decision`` carried
    the field too."""

    summary: str | None = None
    """A one-line preview of what the item was made from, beyond ``description``
    itself: the words as said, cut to the part the item is about when the run
    recorded one. Rule-based (the longest source, truncated). ``None`` when
    ``description`` already is that line -- one source and no model's sentence
    -- since a second copy of it would say nothing ``description`` does not. See
    ``ReviewDecision.summary`` for why a chosen line belongs on the list."""


class SourceUtterance(BaseModel):
    """One utterance an item was drawn from, as the drawer quotes it.

    The text is what module A stored, which is after masking. There is no
    unmasked string anywhere this could have been read from -- privacy.md
    section 2 puts the masker before the first write.
    """

    id: str
    text: str

    excerpt: str | None = None
    """The part of ``text`` the item or decision was made from, cut from it as
    stored -- never reworded, nothing added (``autune_extraction.excerpt``).
    ``None`` when it was made from the whole utterance, when the row is older
    than the offsets, and for a line that is context and not a source: show
    ``text``."""


class EditHistoryEntry(BaseModel):
    """One thing a person did to an item: which fields, when -- never the value
    before or after, and never who (#109, ADR 0003)."""

    kind: Literal["created", "edited", "closed"]
    """``closed``: the item was closed without being finished
    (``service.close_without_finishing``) -- not a correction of it."""
    fields: list[str]
    """For ``edited``: the fields changed, e.g. ``["due_date"]``. Empty for
    ``created``, for ``closed``, and for edits recorded before fields were
    kept."""
    at: datetime


class CloudModelUse(BaseModel):
    """Whether this server sends meeting text to a cloud model -- one fact
    about the deployment, the same for every caller.

    It exists so a screen can say #392's operating rule where a recording is
    put in, and only on a server the rule is about. It carries nothing else on
    purpose: no model name, no implementation name, nothing about the key."""

    in_use: bool


class TeamRead(BaseModel):
    """One of the reader's teams, by name -- the heading over that team's items
    on the board across meetings."""

    id: str
    name: str


class ProjectRead(BaseModel):
    """One of a team's projects (``ext_projects``).

    ``team_id`` is the team it belongs to. A name is unique within a team and
    not across them, and ``GET /projects/mine`` lists every team's the reader
    is on: the team is what tells two projects of one name apart."""

    id: str
    team_id: str
    name: str
    aliases: list[str]
    jira_project_key: str | None = None


class NameSuggestion(BaseModel):
    """A word that came up in several of the team's meetings that no project
    has yet."""

    word: str
    count: int
    """In how many of the latest meetings it came up -- meetings, not mentions."""


class ProjectWrite(BaseModel):
    """A project as a member types it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list)
    jira_project_key: str | None = Field(default=None, max_length=32)


class MaterialWrite(BaseModel):
    """A Drive file a member puts on the team's 자료 screen: a title, and the
    link as pasted. The link is parsed and not kept (``materials.register``)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    link: str = Field(min_length=1, max_length=2000)


class MaterialRead(BaseModel):
    """One of a team's materials (``ext_materials``): a title and which Drive
    file it is. No address -- the screen builds Google's from the id and the
    kind -- and no person."""

    id: str
    team_id: str
    title: str
    drive_file_id: str
    drive_kind: Literal["file", "document", "presentation", "spreadsheets"]
    created_at: datetime


class ProjectPlacement(BaseModel):
    """A person puts a decision or an item in one of the team's projects, or none."""

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None


class ProjectSendRequest(BaseModel):
    """Which of the team's tools a project's minutes go to."""

    model_config = ConfigDict(extra="forbid")

    targets: list[Literal["notion", "slack", "jira", "calendar"]] = Field(min_length=1)


class ProjectSendResult(BaseModel):
    project_id: str
    project_name: str
    target: Literal["notion", "slack", "jira", "calendar"]
    outcome: Literal[
        "created", "updated", "retracted", "not_connected", "no_date", "failed", "held"
    ]
    """``held``: the outbound check refused this copy -- something that looks
    like personal data is in what it would carry: a confirmed decision or item
    of the project, or the team's or project's name in its title. Which of
    them is not known here; the check names a category and no place. A kind of
    ``failed`` that sending again does not mend; changing the text does."""


class ProjectSendReport(BaseModel):
    """What happened to each copy, and how many confirmed rows were left out as
    미분류 -- they belong to no project to be sent as."""

    results: list[ProjectSendResult]
    unsorted: int


class SummaryDecision(BaseModel):
    """A decision as the summary tab lists it: the wording a person confirmed,
    or the model's while it is still pending."""

    id: str
    statement: str
    status: Literal["pending", "confirmed"]
    project_id: str | None = None

    summary: str | None = None
    """``ReviewDecision.summary``: what was said, shown beneath the statement."""

    title: str | None = None
    """``ReviewDecision.title``: the model's short title, which the page of
    minutes leads the row with (2026-10-09). ``None`` for a decision a person
    reworded, and for one without a title."""


MAX_NOTE_CHARS = 2000


ConfirmationAnswer = Literal["commitment", "decision", "not_commitment"]


class MyConfirmation(BaseModel):
    """One ambiguous agreement the reader said, as the web asks them about it.

    ``text`` is their own line, masked as stored. ``answer`` is what they said,
    or ``None`` while they have not; a later answer replaces an earlier one.
    """

    utterance_id: str
    text: str
    answer: ConfirmationAnswer | None = None


class ConfirmationAnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: ConfirmationAnswer


class ExtractionState(BaseModel):
    """What a meeting's 할 일 tab says about its extraction.

    ``failures`` is how many runs in a row raised; zero is "nothing wrong".
    ``will_retry`` says the worker tries again by itself, so the screen does
    not ask a person to. ``requested`` is a "다시 추출" the worker has not
    started yet. Counts and times only: why a run failed is in the server's
    log, by the error's class -- with one distinction the screen needs:
    ``not_published`` says the last failure was passing a stored result on to
    the other modules, so the items and decisions on the board are this run's
    and "could not extract" would be false.

    The last three say why a board can be empty with nothing wrong on record:
    the first run has not finished, never came, or read nothing."""

    extracted_at: datetime | None
    failures: int
    failed_at: datetime | None
    will_retry: bool
    not_published: bool = False
    partly_unread: bool = False
    """The last run stored its rows and could not read part of the transcript
    (``attempts.PARTLY_UNREAD``): the board's items and decisions are this
    run's, and some may be missing. Counted in ``failures`` so that it is
    tried again; never which part, and nothing of it."""
    requested: bool
    requested_at: datetime | None
    in_progress: bool = False
    """A transcript is stored and no run of it is on record yet, nor a failure:
    the first extraction is still going. An empty board is then "not yet", not
    "nothing" (the user, dev, 2026-10-08: no items and no decisions minutes
    after a transcription, and both there after "다시 추출"). Ends by itself:
    with the run, with a failure, or ``attempts.ADOPT_AFTER`` after the
    transcript was stored, when ``overdue`` takes its place."""
    overdue: bool = False
    """The same, and the transcript is older than ``attempts.ADOPT_AFTER``: the
    run did not come -- lost with a worker, or never started. The sweep counts
    it as a failure on its next pass and tries; until then the screen says the
    extraction has not happened, instead of "in progress" for good."""
    read_nothing: bool = False
    """The last run was allowed to read none of the meeting's lines: no speech
    in it has consent on record (``service.consented_utterance_ids``). The run
    went through and found nothing, which is not the same as nothing having
    been said. About the meeting as a whole -- never who did or did not
    consent, and false as soon as any line was read."""


class DueReminderSetting(BaseModel):
    """The caller's own due-date reminders (review of #751).

    ``on`` is their choice: on unless they turned it off. It is one switch
    for five messages -- the due-date reminder, the Monday digest, the
    morning DM, the work-report draft (``work_report_here``,
    ``..._WORK_REPORT``) and the notice after a meeting
    (``after_meeting_here``, ``..._AFTER_MEETING_NOTICE``) -- and a
    deployment turns each on by itself, so the screen is told which of
    them this one sends: ``sent_here`` for the reminder
    (``AUTUNE_EXTRACTION_DUE_REMINDERS``), ``weekly_here`` for the
    Monday digest (``..._WEEKLY_DIGEST``), ``daily_here`` for the morning DM
    (``..._DAILY_DIGEST``). ``sent_here`` alone had the screen say "this
    server sends none yet" on a deployment that sent the two digests."""

    on: bool
    sent_here: bool
    weekly_here: bool = False
    daily_here: bool = False
    work_report_here: bool = False
    after_meeting_here: bool = False


class DueReminderSettingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: bool


class NotificationPause(BaseModel):
    """The caller's own leave dates: no morning DM and no Monday digest from
    ``starts_on`` to ``ends_on``, both days included (the user, 2026-10-05).
    Both ``None`` is no pause. Sent to clear or replace it, and answered with
    what stands. Their own only: nothing here names a person.

    ``on_calendar`` is the person's tick on "내 Google 캘린더에도 추가" (the
    user, 2026-10-06): sent ``true``, the range also goes onto their own
    calendar as one private all-day event; sent ``false``, an event put there
    for an earlier range is removed. **Left out, the calendar stays as it
    stands**: an event already there moves with the dates, and where there is
    none, none is made -- nor is one made in place of an event the person
    deleted in Calendar: that takes the tick. That is what a screen that drew no box sends -- a
    person whose calendar is not connected just now did not untick anything,
    and reading the missing field as ``false`` dropped the event's id, so the
    next ticked save made a second event beside the first (lsh2217's review of
    #922). Answered ``true`` while such an event stands. Nothing is put on a
    calendar without the tick."""

    model_config = ConfigDict(extra="forbid")

    starts_on: date | None = None
    ends_on: date | None = None
    on_calendar: bool | None = None


class NotificationPauseRead(NotificationPause):
    """The caller's pause as it stands, and what else holds their digests back
    here. ``calendar_leave`` says whether this deployment also reads
    out-of-office time from a calendar the person connected
    (``AUTUNE_EXTRACTION_LEAVE_FROM_CALENDAR``), so the screen can say so
    where it is true and stay silent where it is not.

    ``calendar_connected`` is whether the caller has a calendar of their own
    connected -- the box is drawn only then. ``calendar`` is what happened on
    that calendar for the save just made (``leave_calendar.Outcome``), and
    ``None`` on a plain read."""

    calendar_leave: bool = False
    calendar_connected: bool = False
    calendar: (
        Literal[
            "off", "added", "removed", "removal_queued", "not_connected", "not_removed", "failed"
        ]
        | None
    ) = None


class MeetingNoteUpdate(BaseModel):
    """The memo, whole. Blank removes it."""

    body: str = Field(max_length=MAX_NOTE_CHARS)


class GeneratedSummary(BaseModel):
    """A meeting's summary written by a cloud model (#421 v2), shown on the 요약
    tab above B's own rows and marked as a model's. Present only with
    ``summary_impl=llm`` and only while the lines it was written from are
    unchanged."""

    overview: str
    points: list[str]
    model_version: str
    created_at: datetime


class MeetingSummary(BaseModel):
    """S15's 요약 tab, v1 (#421, WBS 4.9): B's own rows in three levels, no model.

    The tab reads these top down -- counts, then the decisions and items
    themselves, then (through the 할 일 tab's drawer) the lines they came from.
    Nothing here is a verbatim quotation: descriptions and statements are the
    same fields the board already lists. A summary written by an LLM over the
    whole meeting is v2, and waits on #392.
    """

    meeting_id: str
    meeting_title: str | None = None
    meeting_started_at: datetime | None = None
    """What the tab heads its page of minutes with (2026-10-09): the meeting's
    own title and day, read from the shared ``meetings`` row. Before, the page
    took its title from the first action item, and a meeting with none had no
    title."""
    decisions: list[SummaryDecision]
    """Confirmed first, then pending, each in the order they were settled.
    A rejected one is not a decision of the meeting and is left out."""
    action_items: list[ActionItemRead]
    """Every item of the meeting, whatever its status."""
    open_questions: int
    """Utterances classified as open questions -- asked, not settled."""
    ambiguous_waiting: int
    """Ambiguous agreements not yet answered: never asked, or asked and within
    the confirmation window."""
    note: str | None = None
    note_updated_at: datetime | None = None
    generated: GeneratedSummary | None = None
    """v2: a model's summary of the whole meeting, or ``None`` when none is
    written or the meeting has changed since."""
    generated_too_long: bool = False
    """v2: no summary was written because the meeting is too long for one, and
    its lines have not changed since that was found. The tab says so instead of
    showing nothing. ``generated`` is then ``None``."""
    projects: list[ProjectRead] = Field(default_factory=list)
    """The team's projects, for grouping ``decisions`` and ``action_items`` by
    their ``project_id`` -- one that is ``None`` is 미분류."""


class ActionItemDetail(ActionItemRead):
    """One item and its evidence, for S18.

    The list carries the source utterances' ids, never their words: a verbatim
    quotation leaves the server only when the drawer asks for one item's.

    That does not make the list free of meeting content. ``description`` is
    drawn from what was said and ``assignee_label`` is a person's name, so
    nothing may forward a list response outside our infrastructure on the
    grounds that it quotes nobody -- ``check_outbound`` catches the shapes of
    personal data, not a Korean name or the sentence that settled a decision.
    """

    sources: list[SourceUtterance]
    """In the order they were spoken, which is the order the argument was made.

    ``source_utterance_ids`` above stays in insertion order: it is built from
    the row alone. This list is read from ``utterances`` anyway, so the spoken
    order comes with it at no extra cost.
    """

    calendar: CalendarState | None = None
    """What this reader may be told about the item and its assignee's
    calendar (``CalendarState``). ``None`` when the item lacks nothing and the
    reader is not the assignee -- the rest is the assignee's to know."""

    context: list[SourceUtterance] = Field(default_factory=list)
    """What was said just before the first source, in spoken order, so a sentence
    with nothing to point at ("다음 주까지 볼게요") can be read with the thing it
    is about. Not what this was drawn from, only the lines around it, which is
    why it is apart from ``sources``. A speaker who did not consent is never here
    -- the line every read of the transcript draws (privacy.md section 5)."""

    related: list[SourceUtterance] = Field(default_factory=list)
    """The lines the summary says it was written from, beyond the commitment itself,
    in spoken order -- a turn that names the thing, a line elsewhere about the
    same subject (``ext_action_item_related``). Empty when the description is the
    quote, a person's own words, or the resolver was not asked to cite. The
    person checking the summary reads these beneath it and corrects the sentence
    if it says more than they show. Same consent filter as ``context``."""

    history: list[EditHistoryEntry] = Field(default_factory=list)
    confirmation_dm_url: str | None = None
    """The reader's own Slack confirmation DM about one of this item's lines --
    only for the person it went to, since nobody else can open it (#680)."""
    """What people did to the item, oldest first (S18, #109). Empty for an item
    the model extracted and nobody has touched since."""


class Assignable(BaseModel):
    """A member of the meeting's team, as the assignee picker offers them.

    Id and name, the shape module A's speaker picker uses for the same
    people; a browser needs no more, and an email is not sent. The screen
    could not set ``assignee_id`` at all before this -- it had a text box
    for a name -- so an item a person added or corrected never reached the
    assignee's calendar or their Jira account, both of which need the
    account and not the name."""

    user_id: str
    name: str


class JiraIssueRead(BaseModel):
    """One open issue of the team's Jira project, as the list shows it.

    Passed through from Jira and stored nowhere (``jira_issues``).
    ``from_autune`` marks an issue Autune made from an action item, so the
    list does not read as a second copy of the board above it."""

    key: str
    summary: str
    status: str | None
    status_category: str | None
    assignee: str | None
    due_date: date | None
    url: str | None
    from_autune: bool


class JiraProjectIssues(BaseModel):
    """One team's Jira project and its open issues.

    ``state`` says why a list is empty when it is not simply empty: the
    team chose no project, its connection needs a person to reconnect, or
    Jira did not answer. ``more`` is true when Jira has more open issues
    than were read."""

    team_id: str
    team_name: str
    project_key: str | None
    state: Literal["ok", "no_project", "needs_reconnect", "unavailable"]
    issues: list[JiraIssueRead] = Field(default_factory=list)
    more: bool = False


class CarriedOverItem(ActionItemRead):
    """An open item from an earlier meeting of the same team, with the meeting
    it was made in -- the popup says where each one came from."""

    meeting_title: str
    meeting_started_at: datetime | None


class CarriedOver(BaseModel):
    """What earlier meetings left open, for the popup a meeting's review opens
    with (PRD 5.2, "incomplete items from previous meetings resurface in the
    next one"; WBS 4.8).

    ``open`` and ``overdue`` count everything; ``items`` is the most urgent
    part of it -- overdue first, then the nearest due date, undated last -- so
    a team with a long tail still sees what matters without a list to scroll.
    """

    open: int
    overdue: int
    stale: int = 0
    """Open items carried through ``STALE_AFTER`` or more meetings."""
    items: list[CarriedOverItem]


# --- review before anything leaves (#246) ------------------------------------


class DecisionReviewUpdate(BaseModel):
    """A person's verdict on one proposed decision. Both fields optional.

    ``pending`` is allowed so a mis-click can be undone. ``statement`` rewords the
    decision; sending the model's own wording back clears the rewording rather than
    storing a copy of it.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "confirmed", "rejected"] | None = None
    statement: str | None = Field(default=None, min_length=1, max_length=2000)


class DecisionCreate(BaseModel):
    """A decision the model missed, typed by a person.

    No ``confidence``: a person typing it is the certainty, as with
    ``ActionItemCreate``. It is confirmed from the moment it exists.
    """

    model_config = ConfigDict(extra="forbid")

    meeting_id: str = Field(pattern=r"^mtg_")
    statement: str = Field(min_length=1, max_length=2000)
    source_utterance_ids: list[str] = Field(default_factory=list)
    """Optional, in spoken order. Each must be an utterance of this meeting."""


class ReviewDecision(BaseModel):
    """One decision as S15 lists it -- proposed by the model or added by a person."""

    id: str
    statement: str
    """What will be sent: the person's rewording when there is one, else the model's."""

    title: str | None = None
    """``statement`` in twenty characters or fewer, for the row's top line
    (``ExtDecision.title``). ``None`` when there is none and whenever a person
    reworded the decision: the title was of the model's sentence."""

    model_statement: str
    """What the model proposed, kept beside the rewording so the screen can show both."""

    confidence: float
    origin: Literal["model", "user"]
    needs_recheck: bool = False
    """A source line was corrected since a person typed or reworded this (#586):
    B cannot correct their wording, so it asks them to look. Cleared by their
    next review."""
    held_back: bool = False
    """Confirmed, and not sent: something that looks like personal data is in
    the statement -- a phone number, an address, an id number -- so the
    outbound check refuses its copy to Notion and Jira, and the agent's tool
    counts it without quoting it. Worked out when the row is read
    (``find_unmasked``), not a record of a send that failed: it is true of a
    team with no tool connected too, and it clears with the rewording that
    removes the value. Neither the value nor its category is here. A text a
    person typed is checked for patterns only, so a name does not set this."""
    status: Literal["pending", "confirmed", "rejected"]
    suggested: bool | None
    """Whether the screen should pre-check it: the confidence clears
    ``candidate_confidence``. ``None`` while that setting is unset -- there is no
    measured line yet, and pre-checking everything or nothing would both be a
    claim the numbers do not support."""

    source_utterance_ids: list[str]

    deleted_source_count: int = 0
    """Sources whose utterance was deleted since -- by module A's rerun of the
    meeting or by a person deleting their own data (#400). They are not in
    ``source_utterance_ids``; the count lets the screen say "근거 발화 삭제됨"
    instead of showing a decision that never had a source, as
    ``ActionItemRead.deleted_source_count`` does for an item."""

    sync_refs: list[ExternalRefRead]
    """One entry per system this decision has been claimed for -- today, at
    most ``notion`` (#30). No drawer exists for a decision (S15 is the whole
    screen), so this rides on the list the way ``ActionItemRead.sync_refs``
    does; a URL is not meeting content. Named ``sync_refs`` rather than
    ``external_refs`` for the same reason as that one -- consistency, though
    ``Decision`` (the contract) carries no field of that name to collide with."""

    summary: str | None = None
    """A one-line preview of what the source utterances said, so the list says
    more than a count. Rule-based, not a model: the longest of them, truncated
    -- see ``service.decision_summaries``. ``None`` when there is nothing to
    summarise (a decision with no sources -- a data problem, not a normal
    state).

    **Still a quotation, on the list, on purpose.** ``ActionItemDetail`` draws
    the line at the *set* of sources -- the drawer's whole evidence, never
    forwarded whole -- not at any single derived line; ``description`` and
    ``assignee_label`` already put content on this same list. One chosen
    sentence is that kind of line, not the other."""


class ReviewAmbiguous(BaseModel):
    """One weak assent and where its question to the speaker stands.

    Read-only here. The speaker answers by DM (``ext_confirmations``); who else may
    answer on their behalf is #246 point 1.
    """

    utterance_id: str
    outcome: Literal["not_asked", "pending", "undecided", "resolved"]
    resolved_kind: str | None


class DecisionDetail(ReviewDecision):
    """One decision and the words it was settled in, for the row S15 expands.

    The list carries the source utterances' ids and one preview line, never the
    whole set of quotations -- the same line ``ActionItemDetail`` draws, and for
    the same reason: a verbatim quotation leaves the server only when one row's
    is asked for.
    """

    sources: list[SourceUtterance]
    """In the order they were spoken -- the proposal first, the sentence that
    settled it last. Empty for a decision a person added without pointing at a
    line, and for one whose every source was deleted (``deleted_source_count``)."""

    context: list[SourceUtterance] = Field(default_factory=list)
    """What was said just before the first source, in spoken order, so a sentence
    with nothing to point at ("다음 주까지 볼게요") can be read with the thing it
    is about. Not what this was drawn from, only the lines around it, which is
    why it is apart from ``sources``. A speaker who did not consent is never here
    -- the line every read of the transcript draws (privacy.md section 5)."""

    related: list[SourceUtterance] = Field(default_factory=list)
    """The lines the model's write-up of this decision says it used, beyond the turns
    it was settled in (``ext_decision_related``), in spoken order. Empty when the
    statement is the assembled line or a person's own wording. Same consent filter
    as ``context``."""


class MeetingReview(BaseModel):
    """Everything in one meeting that needs a person before it goes anywhere."""

    meeting_id: str
    decisions: list[ReviewDecision]
    ambiguous_agreements: list[ReviewAmbiguous]
    action_items: list[ActionItemRead]
    """Items still ``needs_confirmation``, or below the candidate line."""

    pending_decisions: int


class OutboundDecision(BaseModel):
    id: str
    statement: str


class OutboundBlocked(BaseModel):
    """Something confirmed that still may not leave: its text carries personal data.

    The categories, never the values -- the same rule ``assert_masked`` follows
    for an exception message. The screen asks the person to reword it.
    """

    id: str
    kind: Literal["decision", "action_item"]
    categories: list[str]


class Outbound(BaseModel):
    """What of a meeting would leave for Notion, Jira or Slack, and what is
    held back: a read of that, not what lets a copy go.

    A decision nobody confirmed is not in it, and neither is an item still
    waiting for confirmation. It was written as the one list every sender
    would read (#30, #458), and the senders were never moved onto it: each
    copy reads the row it sends, and what stops a text there is the client's
    ``check_outbound`` on the request itself. Its readers are
    ``GET /reviews/{meeting_id}/outbound`` and the agent's
    ``meeting_action_items`` and ``meeting_decisions`` tools. When a text a
    person typed should be checked -- as it is stored, or as it leaves -- is
    open on #1130.
    """

    meeting_id: str
    decisions: list[OutboundDecision]
    action_items: list[ActionItemRead]
    blocked: list[OutboundBlocked]
    """Confirmed, but held back by the personal-data screen. Not in the lists above."""
