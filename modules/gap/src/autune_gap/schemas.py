"""Internal schemas for module C.

Anything another module needs belongs in ``packages/contracts``, not here.

The topic graph below is read by this module's own screen (S20, #48) and by
nothing else. E receives ``GapReport`` and never calls an endpoint, so a shape
that exists only for a visualization would be four modules' business for no
reason — and ``contracts`` is frozen additively, which is a price worth paying
for a payload that crosses a boundary and not for one that does not.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TemplateRead(BaseModel):
    """One domain template, as the S20 rail lists it.

    Not the items. Choosing a template is choosing a name, and the items only
    mean anything next to a meeting — which is what the gap report already is.
    Shipping ten checklists to a screen that shows one name would also put the
    whole of every template into a payload nobody reads.

    ``version`` names every file that contributed (``general.1+feature_planning.1``)
    so a reader comparing two meetings can see they were held to the same
    checklist. See ``autune_gap.template``.
    """

    key: str
    name: str
    version: str
    items: int


class TemplateSelection(BaseModel):
    """Which template a meeting is compared against.

    ``PUT`` takes one of these and returns one, so the caller's own state and
    the server's are the same shape. The key is validated against the loaded
    templates, and an unknown one is a 422 rather than a 404: what is wrong is
    the value, not the address.
    """

    template_key: str


class GapDismissal(BaseModel):
    """Whether somebody called one gap a false positive.

    What ``POST`` and ``DELETE /gaps/{gap_id}/dismiss`` both return, so the
    screen reads the state the server settled on rather than assuming its own
    request won. No timestamp and no dismisser: the screen needs the flag, and
    who pressed the button is exactly what ``gap_gaps`` refuses to store.
    """

    gap_id: str
    meeting_id: str
    dismissed: bool


class GapCarry(BaseModel):
    """Whether somebody sent one gap on to the next meeting (#824).

    What ``POST`` and ``DELETE /gaps/{gap_id}/carry`` both return, the same
    shape as ``GapDismissal`` and for the same reasons: the flag the server
    settled on, no timestamp and nobody's id.
    """

    gap_id: str
    meeting_id: str
    carried: bool


class TemplateItemRead(BaseModel):
    """One checklist item beside what the meeting did with it.

    ``coverage`` is ``covered``, ``partial`` or ``missing``, and ``None`` when
    the meeting has no topic graph yet — nothing has been compared, which is
    not the same as everything being covered and must not render as it. See
    ``service.template_comparison``.

    ``gap_id`` points at the row on the left of the screen, so the rail and the
    gap list are the same finding seen twice rather than two lists a reader has
    to reconcile. Null for a covered item, which raised nothing.
    """

    key: str
    category: str
    item: str
    coverage: str | None = None
    gap_id: str | None = None
    dismissed: bool = False
    """Somebody called this gap a false positive. The item is still not covered
    — the row stays and threshold tuning reads it (ADR 0006) — so the rail says
    both rather than quietly promoting the item to covered."""


class TemplateComparison(BaseModel):
    """The S20 rail: which checklist this meeting is held to, and how far it
    got with each item.

    A superset of ``TemplateSelection`` — ``template_key`` is still the first
    field, so a caller that only wanted to know which template is in force
    reads the same key off the same endpoint.

    ``analysed`` is whether the meeting has a topic graph at all. Without one
    ``detect.compare`` raises nothing by design (an empty graph says extraction
    found nothing, not that the meeting discussed nothing), so every item would
    otherwise read as covered — the exact false statement the rail exists to
    avoid making.
    """

    template_key: str
    name: str
    version: str
    analysed: bool
    items: list[TemplateItemRead] = Field(default_factory=list)


class TopicNodeRead(BaseModel):
    """One node of the graph S20 draws.

    Carries ``betweenness``, which ``autune_contracts.Topic`` does not. The
    contract gives E what it scores a meeting on; a renderer also wants the
    topic that *joined* two conversations, and that is the second number
    ``gap_topics`` keeps beside PageRank rather than blending into it.

    No utterance ids here. The evidence behind a topic is transcript content,
    it is already in the report, and a graph that carries it makes every node
    of a long meeting ship its quotations whether or not anyone opens one.
    """

    id: str
    label: str
    centrality: float = Field(ge=0, le=1)
    betweenness: float = Field(ge=0, le=1)


class TopicEdgeRead(BaseModel):
    """One relation between two topics, exactly as ``gap_topic_edges`` holds it.

    A symmetric relation is stored as two rows, one per direction (see
    ``models.GapTopicEdge``), and both come back here. Collapsing the pair on
    the way out would be a rule that ``relation == "co_occurs"`` is undirected,
    and relation extraction (#32) is about to produce directed triples where
    the same collapse would lose which topic acted on which. A renderer that
    wants one line per pair drops the direction it does not need; this endpoint
    does not decide that for it.
    """

    source_topic_id: str
    target_topic_id: str
    relation: str
    weight: float = Field(gt=0, le=1)


class TopicGraphRead(BaseModel):
    """This meeting's topic graph, for drawing.

    Nodes come in the same order as ``GapReport.topics`` — most central first —
    so a screen showing both never has to reconcile two orderings.

    Deliberately no participation matrix. Who spoke on a topic is in the
    report, keyed by topic id, and the graph is the one surface where a node
    could quietly acquire a per-person number attached to a picture. See
    docs/architecture/privacy.md section 3.
    """

    meeting_id: str
    nodes: list[TopicNodeRead] = Field(default_factory=list)
    edges: list[TopicEdgeRead] = Field(default_factory=list)


class EvidenceRead(BaseModel):
    """One utterance a verdict rests on, as S20 quotes it.

    Masked text -- module A masks before it writes, and there is no other form
    to read. No speaker: the quote says what was said and when, which is what a
    reader checks a verdict against; who said it is the transcript's to show.
    """

    utterance_id: str
    start_sec: float
    text: str


class ScorePartRead(BaseModel):
    """One term of the risk score's weighted mean (``detect.ScorePart``)."""

    key: str
    weight: float
    value: float


class ScoreBreakdownRead(BaseModel):
    """How a gap's risk score was reached (``detect.score_breakdown``).

    ``score`` is the stored ``risk_score``. A breakdown recomputed today that
    no longer reaches it -- a weight, the template or the topic's
    participation moved since the meeting was scored -- is not sent at all
    rather than sent beside a number it does not add up to.
    """

    parts: list[ScorePartRead] = Field(default_factory=list)
    damping: float | None = None
    score: float


class GapExplanationRead(BaseModel):
    """Why one gap was raised: its verdict, what it rests on, how it scored.

    ``basis`` is the evidence the classification read (``detect.classify``):

    - ``topic`` -- a topic matched and sat below ``partial_centrality``;
      ``topic_label`` and ``topic_centrality`` say which and how central.
    - ``keyword`` -- no topic, but an item keyword was said; ``matched_keywords``
      are the ones heard.
    - ``meaning`` -- no topic and no keyword; the speech was read by meaning
      (``semantic.heard``). Nothing to quote: the embedding is not kept.
    - ``none`` -- missing. Nothing matched, so ``evidence`` is empty and
      ``keywords`` is what the meeting was searched for.
    """

    gap_id: str
    coverage: str | None = None
    basis: str
    topic_label: str | None = None
    topic_centrality: float | None = None
    keywords: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRead] = Field(default_factory=list)
    breakdown: ScoreBreakdownRead | None = None
    carried: bool = False
    """Whether somebody sent this gap on to the next meeting (#824). Here rather
    than on ``GapReport``: the contract is E's business, and E has no use for it."""


class GapExplanations(BaseModel):
    """Everything S20 shows beside a gap that ``GapReport`` does not carry.

    Module C's own read, not a contract: E scores a meeting on ``GapReport`` and
    never explains a verdict to anyone. The meeting's title and date are here
    so the screen's breadcrumb names the meeting rather than its id; module C
    reads ``meetings`` and writes nothing to it (invariant 4).
    """

    meeting_id: str
    meeting_title: str
    meeting_date: datetime
    """When the meeting was held: ``started_at``, or when its row was made if it
    never got a start time -- the rule ``tools._previous_analysed`` orders a
    team's meetings by. A recording uploaded after the fact has no start time,
    and a breadcrumb with no date at all was most meetings on screen."""
    partial_centrality: float
    high_threshold: float
    medium_threshold: float
    gaps: list[GapExplanationRead] = Field(default_factory=list)


class TeamGapRead(BaseModel):
    """One open gap on the team-wide list -- the sidebar's "갭 리포트" (#550).

    The gap as S20 shows it, plus the meeting it belongs to so the row can say
    where it came from and link back to that meeting's report.

    **No people and no topics.** S20 shows a gap beside the topics it was
    inferred from and who was silent on them; a list across every meeting is
    where reading that along a person would be easiest, and it is the reading
    privacy.md section 3 forbids. A reader who wants the why opens the meeting.
    """

    gap_id: str
    meeting_id: str
    meeting_title: str
    meeting_date: datetime
    """When the meeting started, or when it was registered if it never did --
    the same date S20's breadcrumb shows."""
    category: str
    title: str
    severity: str
    risk_score: float = Field(ge=0, le=1)
    template_item: str | None = None
    suggested_question: str | None = None
