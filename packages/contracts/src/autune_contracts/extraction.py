"""B -> E. Classifications, action items, and unresolved agreement.

B -> D. The Jira issues a team has open, for the pre-meeting brief (#436).
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import AwareDatetime, Field, model_validator

from ._base import ContractModel, Payload, TeamPayload
from .enums import ActionStatus, ExternalSystem, UtteranceKind


class ExternalRef(ContractModel):
    system: ExternalSystem
    url: str
    external_id: str | None = None


class ActionItem(ContractModel):
    id: str = Field(pattern=r"^act_")
    description: str
    assignee_id: str | None = Field(default=None, description="None until someone is assigned.")
    assignee_label: str | None = None
    due_date: date | None = None
    source_utterance_ids: list[str] = Field(default_factory=list)
    status: ActionStatus = ActionStatus.NEEDS_CONFIRMATION
    confidence: float = Field(ge=0, le=1)
    external_refs: list[ExternalRef] = Field(default_factory=list)


STANCE_MIN_IDENTIFIED_PER_ROLE = 3
"""A role is reported only when at least this many identified people held it."""


class RoleStance(ContractModel):
    """How many people in one role backed a decision or raised a concern on it.

    Counts, never identities. There is no participant id, user id or speaker
    label here, and adding one is a privacy violation rather than an additive
    change: who opposed a decision is a per-person record of behaviour in a
    meeting, the same shape docs/architecture/privacy.md section 3 forbids for
    speaking ratios.

    A role appears only when the meeting had at least
    `STANCE_MIN_IDENTIFIED_PER_ROLE` identified people in it. In a small team a
    role is a person, and a count over one person is that person's stance.

    People are counted by distinct ``user_id``, each in at most one of the two
    counts, so ``supporting + concerns <= identified``. A person who spoke on the
    decision without doing either is in neither count, so this is not coverage.

    **A unanimous role is not representable.** ``supporting == identified`` or
    ``concerns == identified`` says what every person in the role did, which is
    each person's stance however many of them there are -- the gap k-anonymity
    leaves. A producer leaves such a role out. A count of zero is allowed: "nobody in
    the role raised a concern" is how most roles look after most decisions, and
    refusing it would drop nearly every row.

    What remains is a lopsided split -- four of five supporting, one concern. It
    says one person dissented, not which; anyone who knows the other four's
    stances can still tell. Accepted as the residual risk of a count, and not
    gated further here, because every stricter rule empties most roles in a team
    meeting.
    """

    role: str
    identified: int = Field(
        ge=STANCE_MIN_IDENTIFIED_PER_ROLE,
        description="Identified people in the role at the meeting, carried so the gate is checked.",
    )
    supporting: int = Field(ge=0, description="People in this role who voiced support.")
    concerns: int = Field(ge=0, description="People in this role who raised a concern.")

    @model_validator(mode="after")
    def _counts_fit_the_role(self) -> RoleStance:
        if self.supporting + self.concerns > self.identified:
            raise ValueError(
                "supporting and concerns together cannot exceed the people identified in the role"
            )
        if self.identified in (self.supporting, self.concerns):
            raise ValueError("a unanimous role reveals every person's stance and is left out")
        return self


class Decision(ContractModel):
    """A decision the meeting settled.

    Distinct from a `Classification` with ``kind="decision"``: that marks one
    utterance, while a decision is often spread over several. B owns deciding
    *what counts as a decision in this meeting*; D owns deciding *whether it is
    the same decision as one from a past meeting*.
    """

    id: str = Field(pattern=r"^dec_")
    statement: str = Field(description="The decision as settled, in one sentence.")
    source_utterance_ids: list[str] = Field(
        default_factory=list, description="One decision may span several utterances."
    )
    confidence: float = Field(ge=0, le=1)
    stance_by_role: list[RoleStance] = Field(
        default_factory=list,
        description=(
            "Per role, never per person. Empty when no role cleared the gate or "
            "stance was not computed; the two are not distinguished."
        ),
    )


class Classification(ContractModel):
    utterance_id: str = Field(pattern=r"^utt_")
    kind: UtteranceKind
    confidence: float = Field(ge=0, le=1)
    nli_verified: bool = False


class AmbiguousAgreement(ContractModel):
    """Assent too weak to treat as a commitment; the speaker was asked to confirm."""

    utterance_id: str = Field(pattern=r"^utt_")
    reason: str
    confirmation_sent: bool = False


class ExtractionResult(Payload):
    action_items: list[ActionItem] = Field(default_factory=list)
    decisions: list[Decision] = Field(
        default_factory=list,
        description="Consumed by D to build decision lineage across meetings.",
    )
    classifications: list[Classification] = Field(default_factory=list)
    ambiguous_agreements: list[AmbiguousAgreement] = Field(default_factory=list)


AGENDA_TITLE_MAX = 200
"""Characters in one issue title: the largest value a consumer will receive,
so it can size what it renders against a known bound. The producer shortens to
this. It is a bound, not a display length -- a consumer still cuts to its own
(D's brief shows six issues and cuts a title at 100), and that cut stays
necessary (#491 review)."""

JIRA_ISSUE_URL = r"^https://[A-Za-z0-9.-]+/browse/[A-Z][A-Z0-9_]*-[0-9]+$"
"""An issue's browse link on a Jira site: https, a host, ``/browse/KEY-12``.

A consumer puts ``url`` in an ``href`` (D's brief panel does), so anything but
this shape -- a ``javascript:`` URL above all -- is refused at validation rather
than trusted to every renderer."""


class AgendaIssue(ContractModel):
    """One open Jira issue a team's meetings may take up.

    What a brief line needs and nothing else: no assignee, no description body.
    """

    title: str = Field(
        min_length=1,
        max_length=AGENDA_TITLE_MAX,
        description="The issue's summary: masked item text, one line.",
    )
    key: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*-[0-9]+$")
    status: str | None = Field(default=None, description="A display name, e.g. 진행 중.")
    url: str | None = Field(default=None, pattern=JIRA_ISSUE_URL)


AGENDA_PUBLISH_EVERY = timedelta(minutes=5)
"""How often the producer republishes every team's agenda, changed or not."""

AGENDA_STALE_AFTER = timedelta(minutes=30)
"""A snapshot older than this -- six missed publishes -- is **empty** to a
consumer, whatever it lists (#491 review). The producer stops publishing for a
team once nothing of its is left to publish: its last Jira-linked item deleted
by a person, by a user deleting their own data, or by the retention sweep, all
of which take the item's Jira link with it. Without this rule the last snapshot
would keep showing deleted titles for good (invariant 11)."""


class TeamAgenda(TeamPayload):
    """Every open issue made from the team's action items, as of ``as_of`` (#436).

    A snapshot, not a change: each one replaces the last, and an empty
    ``issues`` means the team has none open. Republished every
    ``AGENDA_PUBLISH_EVERY`` whether or not anything changed. Events can arrive
    out of order, so a consumer keeps the one with the latest ``as_of`` -- and
    treats it as empty once it is older than ``AGENDA_STALE_AFTER``. The producer
    decides the order (most pressing first) and caps the list; a consumer shows
    the head.
    """

    as_of: AwareDatetime
    """With an offset. A consumer compares snapshots by ``as_of``, and a naive
    time against an aware one raises instead of comparing (#491 review)."""
    issues: list[AgendaIssue] = Field(default_factory=list)


ACTION_PROGRESS_PUBLISH_EVERY = timedelta(minutes=10)
"""How often the producer republishes every team's action progress, changed or
not -- the same cadence as the Jira status read-back (#548)."""

ACTION_PROGRESS_STALE_AFTER = timedelta(minutes=30)
"""A snapshot older than this -- three missed publishes -- is **unknown** to a
consumer, not empty: a rate computed from it would be presented as current."""

ACTION_PROGRESS_TODAY_ZONE = "Asia/Seoul"
"""Whose "today" ``overdue`` is counted against: the date of ``as_of`` in this zone.

Teams have no time zone yet, and a server on UTC would otherwise count a day
late between 00:00 and 09:00 in Seoul, so B's board and E's dashboard could
disagree on the same item (#619 review). One zone for every team until a
team setting exists; changing it is a contract change."""

ACTION_PROGRESS_WINDOW = timedelta(days=91)
"""Meetings counted: those created within this of ``as_of``. Thirteen weeks,
the default 90-day retention rounded up to whole weeks."""


class MeetingActionProgress(ContractModel):
    """One meeting's action items as counts, as of the snapshot (#605).

    Counts and the meeting's id, nothing else: no assignee, no title, no item
    id. Adding an assignee is a privacy violation rather than an additive
    change -- it would make a per-person completion record
    (docs/architecture/privacy.md section 3), the same rule as ``RoleStance``.
    """

    meeting_id: str = Field(pattern=r"^mtg_")
    confirmed: int = Field(
        ge=1,
        description=(
            "Items past needs_confirmation; deleted items are gone, and an item closed "
            "without being finished is not counted."
        ),
    )
    done: int = Field(
        ge=0,
        description=(
            "Confirmed items in status done that were finished; one closed without being "
            "finished is in neither count."
        ),
    )
    overdue: int = Field(
        ge=0,
        description=(
            "Confirmed, not done, due before the date of as_of in Asia/Seoul -- the "
            "board's rule. No per-team time zone exists yet."
        ),
    )

    @model_validator(mode="after")
    def _counts_hold_together(self) -> MeetingActionProgress:
        if self.done > self.confirmed:
            raise ValueError("done exceeds confirmed")
        if self.overdue > self.confirmed - self.done:
            raise ValueError("overdue exceeds the confirmed items not done")
        return self


class TeamActionProgress(TeamPayload):
    """A team's action-item counts per meeting, as of ``as_of`` (#605).

    A snapshot, not a change: each one replaces the last. Republished every
    ``ACTION_PROGRESS_PUBLISH_EVERY`` for every team with a meeting inside
    ``ACTION_PROGRESS_WINDOW``, whether or not anything changed. Events can
    arrive out of order, so a consumer keeps the one with the latest ``as_of``
    and treats it as unknown once it is older than
    ``ACTION_PROGRESS_STALE_AFTER``. A fresh snapshot with no ``meetings`` is a
    fact -- nothing confirmed in the window -- not "unknown".

    **How a consumer may use it.** Module E shows team totals over the window
    only. A meeting whose confirmed items are all one person's makes that
    meeting's counts that person's completion record, so no consumer shows a
    count per meeting, and none feeds them to a meeting report, a direct
    message or a prediction feature (#605 review). Widening that is a privacy
    decision, not a code change.
    """

    as_of: AwareDatetime
    """With an offset; snapshots are compared by it."""
    meetings: list[MeetingActionProgress] = Field(default_factory=list)
    """Meetings inside the window with at least one confirmed item, once each."""

    @model_validator(mode="after")
    def _each_meeting_once(self) -> TeamActionProgress:
        ids = [m.meeting_id for m in self.meetings]
        if len(ids) != len(set(ids)):
            raise ValueError("a meeting appears more than once")
        return self
