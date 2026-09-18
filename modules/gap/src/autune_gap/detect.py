"""Steps 6 and 7 of docs/modules/gap.md: template comparison and risk scoring.

Pure functions, like ``graph``. No database, no model, no network — ``service``
reads the stored topic graph, hands it here, and writes back what comes out. So
what counts as a gap, and how risky it is, can be argued with and tested without
starting Postgres.

**The metric is precision, not recall** (docs/modules/gap.md, "Metric"). A false
gap costs the team's trust in every other gap on the screen; a missed one costs
nothing they did not already not have. Every judgement below leans that way: a
partial finding is damped, an unmeasurable signal is dropped rather than assumed
to be bad, and only ``high`` reaches a reader by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from autune_gap.graph import topic_key
from autune_gap.template import Template, TemplateItem


class Coverage(StrEnum):
    """How far the meeting got with one template item."""

    COVERED = "covered"
    """A topic matched and the meeting gave it real weight. No gap."""

    PARTIAL = "partial"
    """Named, but barely — a topic on the edge of the graph, or one the roles
    that had to be in the conversation said nothing on."""

    MISSING = "missing"
    """Nothing in the meeting matched it.

    S20 shows the three states side by side; the first raises nothing and the
    other two raise a gap.
    """


@dataclass(frozen=True)
class TopicView:
    """One stored topic, as much of it as comparison needs.

    ``silent_share`` is how much of the meeting's consenting membership was
    silent on this topic, in 0..1 — an aggregate over a topic, never a total
    along a person (docs/architecture/privacy.md section 3). ``None`` means
    there was nobody to measure it over, which is a missing signal and not a
    quiet zero; see ``score``.
    """

    id: str
    label: str
    centrality: float
    silent_share: float | None = None


@dataclass(frozen=True)
class Thresholds:
    """Everything numeric, from ``config``. Nothing here is hardcoded (#35)."""

    high: float
    medium: float
    partial_centrality: float
    partial_damping: float
    weight_template: float
    weight_coverage: float
    weight_participation: float


@dataclass(frozen=True)
class Finding:
    """One gap, ready for ``service`` to store.

    Ids only, no transcript text: the topic labels a reader sees are read back
    out of the topic rows on the way to the report.
    """

    item_key: str
    category: str
    template_item: str
    title: str
    question: str
    coverage: Coverage
    risk_score: float
    severity: str
    topic_ids: tuple[str, ...]


MISSING_TITLE = "{item} — 논의되지 않았습니다"
PARTIAL_TITLE = "{item} — 충분히 다뤄지지 않았습니다"
"""Gap titles are product copy a reader sees on S20, so they are Korean
(/CLAUDE.md section 0). They are composed here rather than carried per item in
the template file because the wording belongs to the coverage state and not to
the item — per item it would be the same two sentences written ten times, and
they would drift."""


def compare(
    template: Template,
    topics: list[TopicView],
    thresholds: Thresholds,
    *,
    roles_known: bool,
) -> list[Finding]:
    """Findings for one meeting, riskiest first.

    A covered item produces nothing. Ties break on the item's position in the
    template, which is stable, so a re-run of the same meeting stores the same
    gaps in the same order instead of shuffling them under equal scores.

    **A meeting with no topics raises no gaps at all.** Every item would be
    missing, and the template would produce its whole checklist as findings
    about a meeting the pipeline failed to read — the graph being empty says
    extraction found nothing, not that the meeting discussed nothing. It is the
    one case where "missing" carries no information, and with NER recall on
    spoken Korean where it is (docs/modules/gap.md, "Step 1 as built") it is a
    case that happens. Saying nothing is the honest output, and the metric is
    precision.
    """
    if not topics:
        return []

    scored: list[tuple[int, Finding]] = []

    for position, item in enumerate(template.items):
        matched = match(item, topics)
        coverage = classify(item, matched, thresholds, roles_known=roles_known)
        if coverage is Coverage.COVERED:
            continue

        risk = score(item, matched, coverage, thresholds)
        template_for = MISSING_TITLE if coverage is Coverage.MISSING else PARTIAL_TITLE
        scored.append(
            (
                position,
                Finding(
                    item_key=item.key,
                    category=item.category,
                    template_item=item.item,
                    title=template_for.format(item=item.item),
                    question=item.question,
                    coverage=coverage,
                    risk_score=risk,
                    severity=severity_of(risk, thresholds),
                    topic_ids=tuple(topic.id for topic in matched),
                ),
            )
        )

    scored.sort(key=lambda entry: (-entry[1].risk_score, entry[0]))
    return [finding for _, finding in scored]


def match(item: TemplateItem, topics: list[TopicView]) -> list[TopicView]:
    """Topics that look like this item, most central first.

    Containment either way, over the same normalisation the graph keys topics
    by: a keyword inside a longer label, and a label inside a longer keyword,
    both count. Nothing here merges meanings — ``graph.topic_key`` declines to
    merge one topic into another for the same reason, and a comparison that
    guessed would report a meeting as having covered something it did not.

    This is the rule v1 measures precision against. It is deliberately dumb, and
    what replaces it — embeddings over the template items — is then a change
    with a number attached rather than a better idea.
    """
    hits = [topic for topic in topics if _looks_like(item, topic)]
    return sorted(hits, key=lambda topic: (-topic.centrality, topic.id))


def _looks_like(item: TemplateItem, topic: TopicView) -> bool:
    label = topic_key(topic.label)
    return any(word in label or label in word for word in item.keywords)


def classify(
    item: TemplateItem,
    matched: list[TopicView],
    thresholds: Thresholds,
    *,
    roles_known: bool,
) -> Coverage:
    """Covered, partial, or missing — the three states S20 shows.

    Partial is the meeting having named the thing without settling it: a topic
    the graph put at the edge, or one that the roles who had to weigh in said
    nothing on. The second half is inert today — ``participants.role`` is never
    written (#22, awaiting A) — and ``roles_known`` says so explicitly rather
    than letting an unwritten column read as "no engineer spoke", which would
    raise that partial gap in every meeting of every template.
    """
    if not matched:
        return Coverage.MISSING

    best = matched[0]
    if best.centrality < thresholds.partial_centrality:
        return Coverage.PARTIAL
    if roles_known and item.roles and best.silent_share == 1.0:
        return Coverage.PARTIAL
    return Coverage.COVERED


def score(
    item: TemplateItem,
    matched: list[TopicView],
    coverage: Coverage,
    thresholds: Thresholds,
) -> float:
    """Risk in 0..1: template weight, how thinly the meeting covered it, and how
    much of the room was silent on it.

    **A signal that cannot be measured is dropped and the rest renormalised**,
    never scored as a zero or a one. Module E does the same with
    ``_decision_density`` when a meeting reached no decisions — a weighted
    heuristic that quietly substitutes for an absent input is reporting a number
    nobody measured.

    Two of the three are unmeasurable for a missing item, which leaves the
    item's own weight: there is no topic to read a centrality off, and none to
    read a silence off either. **So a missing item scores exactly what the
    template author said it was worth**, and that is the honest reading — the
    meeting's contribution to a missing finding is the classification, not the
    number. Charging it a full 1.0 for "no coverage" instead, which is what this
    did first, added the same constant to every missing item and pushed the
    whole checklist into ``high``: a score that looks measured and is not.

    A partial finding is damped on the way out. "Named but thin" is a weaker
    claim than "never came up", and the metric is precision.
    """
    parts: list[tuple[float, float]] = [(thresholds.weight_template, item.weight)]

    best = matched[0] if matched else None
    if best is not None:
        parts.append((thresholds.weight_coverage, 1.0 - best.centrality))
        if best.silent_share is not None:
            parts.append((thresholds.weight_participation, best.silent_share))

    total = sum(weight for weight, _ in parts)
    if total <= 0:
        raise ValueError("risk weights sum to zero; check the AUTUNE_GAP_WEIGHT_ settings")

    raw = sum(weight * value for weight, value in parts) / total
    if coverage is Coverage.PARTIAL:
        raw *= thresholds.partial_damping

    # Rounded before it is compared against a threshold. A missing item of
    # weight 0.7 comes out of the weighted mean as 0.6999999999999998, and
    # `severity_of` then reads it as `medium` — an item the template author
    # weighted exactly at the threshold, demoted by float residue at the
    # sixteenth decimal place. The score is a heuristic a screen shows to two
    # places; six is past anything it means and short of where the residue is.
    return min(1.0, max(0.0, round(raw, 6)))


def severity_of(risk: float, thresholds: Thresholds) -> str:
    """``high`` at or above the threshold, ``medium`` down to the second one,
    ``low`` below. Only ``high`` is surfaced by default — modules/gap/CLAUDE.md.

    Returns the string ``gap_gaps.severity`` stores, which
    ``autune_contracts.GapSeverity`` mirrors.
    """
    if risk >= thresholds.high:
        return "high"
    if risk >= thresholds.medium:
        return "medium"
    return "low"
