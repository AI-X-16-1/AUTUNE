"""Scoring for module C's evaluation harness.

Pure functions over sets of template item keys: no database, no model, no
corpus. The harness composes these and the unit tests exercise them directly,
which is the only reason any of this can be tested without a migrated Postgres
and a 500 MB spaCy pipeline.

**The metric is precision** — 0.70+ at six weeks, 0.82+ at three months
(docs/modules/gap.md, "Metric"). Recall is computed and printed because it is
free and it says whether suppression went too far, but it is not the target: a
false gap costs the team's trust in every other gap on the screen, and a missed
one costs nothing they did not already not have.

**Precision is measured over the gaps a reader actually sees**, which is the
``high`` band and nothing else (modules/gap/CLAUDE.md). A `medium` false
positive is not a false statement to anybody until something surfaces it. The
report carries the all-severity figure beside it, because the two moving apart
is the interesting thing — it means the bands are doing the work rather than the
comparison.

**Nothing is scored against a number nobody measured.** Precision over zero
raised gaps is undefined, and both 0.0 and 1.0 would be a claim: returning
``None`` is the same convention ``detect.score`` uses for a signal it cannot
read, and module E's ``_decision_density`` for a meeting that reached no
decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TARGET_PRECISION_SIX_WEEKS = 0.70
TARGET_PRECISION_THREE_MONTHS = 0.82
"""docs/product/prd.md section 12, and docs/modules/gap.md, "Metric"."""

PARTIAL = "partial"
"""``AUTUNE_GAP_PARTIAL_CENTRALITY`` turned a passing mention into a gap. A
threshold problem, and the only cause a config change can fix."""

EXTRACTION = "extraction"
"""The meeting said a noun that this item's keywords do match, and extraction
never turned it into a topic. A step-1 problem (#278): fix the extractor and
the gap stops being raised, with no template edit at all."""

KEYWORD = "keyword"
"""The noun is in the graph as a topic and the item's keywords do not name it.
A template problem, and the only cause widening a keyword list can fix."""

NO_NOUN = "no-noun"
"""The meeting settled the item without saying any noun that could name it —
"이건우님이 맡고 다음 주까지" settles ownership with a verb and a date.

**Neither a keyword list nor a better extractor reaches this one.** Matching
keywords against topic labels is lexical, and what settled the item is
grammatical. A false positive here says the comparison rule has a ceiling, not
that it is mistuned, so it is counted apart from the two that can be fixed.
"""

CAUSES: tuple[str, ...] = (PARTIAL, EXTRACTION, KEYWORD, NO_NOUN)


def classify_false_positive(
    *,
    partial: bool,
    expected: tuple[str, ...],
    topic_labels: frozenset[str],
    keywords: tuple[str, ...],
) -> str:
    """Why this gap was raised on an item the meeting had settled.

    ``expected`` is the hand-labeled nouns a reader would point at as settling
    the item, ``topic_labels`` is what extraction actually produced, and
    ``keywords`` is the template item's own list. All three already normalised
    by ``graph.topic_key`` — the caller does it, because that is where the
    labels come from.

    The order matters. A partial finding is a threshold decision and says
    nothing about keywords or extraction, so it is answered first. Then: no
    noun to find at all, a noun nobody extracted, or a noun extracted and not
    named.

    A noun that is in the graph *and* matched by the keywords cannot be here at
    all — the item would have been covered rather than raised — so that case is
    the ``KEYWORD`` answer by construction.

    **"In the graph" means a topic label contains the whole expected term**, and
    not ``detect.match``'s containment-either-way. The two differ on a truncated
    label, and that difference is the point: the meeting said "외부 전송
    실패인데", extraction dropped the particle-carrying tail and stored the topic
    as "외부 전송", and reading a label *inside* the expected term as a match
    answered ``KEYWORD`` for what is a step-1 truncation. A topic that carries
    half the noun a reader pointed at is not that noun, and no keyword list
    should be widened on its account.
    """
    if partial:
        return PARTIAL
    if not expected:
        return NO_NOUN
    if not any(term in label for label in topic_labels for term in expected):
        return EXTRACTION
    return KEYWORD


@dataclass(frozen=True)
class CaseScore:
    """One meeting's outcome, as sets of template item keys.

    Keys, never gap titles or topic labels: a title is composed from a template
    file and a label is transcript text, and this report is printed to a
    terminal and pasted into issues.
    """

    case_id: str
    template_key: str
    real: frozenset[str]
    """Hand-labeled: the items this meeting genuinely left unsettled."""

    raised_high: frozenset[str]
    """What the pipeline surfaced. Precision is measured here."""

    raised_any: frozenset[str]
    """Every severity, including the ones no screen shows by default."""

    raised_partial: frozenset[str]
    """Of the surfaced gaps, the ones raised as *partial* — the meeting named
    the item and left it at the edge of the graph — as opposed to *missing*.

    Read off ``gap_related_topics`` rather than stored on the row: a missing
    item was inferred from the absence of a topic and points at none, and a
    partial one points at the topic it was inferred from (see
    ``service.detect_gaps``). The distinction is what says which of the two
    rules a false positive came from, and they fail for different reasons — a
    missing false positive means the template's keywords could not see an item
    the meeting settled, a partial one means ``partial_centrality`` turned a
    passing mention into a gap.
    """

    topics: int
    """How many topics extraction produced. A case scoring badly with two
    topics is a different problem from one scoring badly with thirty."""

    fp_cause: dict[str, str] = field(default_factory=dict)
    """Item key -> why its false positive happened, for the ones raised on an
    item the meeting had settled. One of ``CAUSES``.

    Filled by the runner, which is the only place that has the graph, the
    template and the labels at once. Empty for a case with no false positives,
    and for a score built by a test that does not care.
    """

    @property
    def true_positives(self) -> frozenset[str]:
        return self.raised_high & self.real

    @property
    def false_positives(self) -> frozenset[str]:
        """Raised and surfaced, and the meeting had settled it. What the metric
        exists to count."""
        return self.raised_high - self.real

    @property
    def missed(self) -> frozenset[str]:
        return self.real - self.raised_high

    def cause(self, item_key: str) -> str:
        """Why this false positive happened, or ``"unclassified"``.

        Unclassified means the case carries no ``evidence`` labels, so the
        three-way split cannot be computed for it. Reported as its own bucket
        rather than folded into one of the real causes.
        """
        return self.fp_cause.get(item_key, "unclassified")


@dataclass(frozen=True)
class Report:
    cases: tuple[CaseScore, ...]

    @property
    def precision(self) -> float | None:
        """Over the ``high`` band. ``None`` when nothing was raised at all."""
        return _ratio(self._sum("true_positives"), self._sum("raised_high"))

    @property
    def precision_any_severity(self) -> float | None:
        raised = sum(len(case.raised_any) for case in self.cases)
        correct = sum(len(case.raised_any & case.real) for case in self.cases)
        return _ratio(correct, raised)

    @property
    def recall(self) -> float | None:
        """``None`` when no case labels a real gap, which is a set of meetings
        that settled everything — a legitimate set to score precision on and
        one recall means nothing over."""
        return _ratio(self._sum("true_positives"), self._sum("real"))

    @property
    def meets_six_week_target(self) -> bool:
        return self.precision is not None and self.precision >= TARGET_PRECISION_SIX_WEEKS

    @property
    def false_positives_by_cause(self) -> dict[str, int]:
        """How many false positives each of the two rules produced.

        The headline number says the pipeline is over- or under-shooting; this
        says which half to go and look at.
        """
        counts = dict.fromkeys(CAUSES, 0)
        for case in self.cases:
            for item in case.false_positives:
                cause = case.cause(item)
                counts[cause] = counts.get(cause, 0) + 1
        return {cause: count for cause, count in counts.items() if count}

    @property
    def fixable_false_positives(self) -> int:
        """False positives a change to this module could remove.

        Everything but ``NO_NOUN`` and ``unclassified``. Worth a number of its
        own: a precision figure says how far off the target is, and this says
        how much of the distance is even reachable from here.
        """
        by_cause = self.false_positives_by_cause
        return sum(
            count for cause, count in by_cause.items() if cause in (PARTIAL, EXTRACTION, KEYWORD)
        )

    @property
    def empty_graph_cases(self) -> tuple[CaseScore, ...]:
        """Cases extraction found no topics in.

        ``detect.compare`` raises nothing for these on purpose, so they
        contribute no gaps to precision and drag recall down for a reason that
        is about step 1 rather than about steps 6 and 7. Worth seeing rather
        than silently averaging over.
        """
        return tuple(case for case in self.cases if case.topics == 0)

    def _sum(self, attribute: str) -> int:
        return sum(len(getattr(case, attribute)) for case in self.cases)


def score(cases: list[CaseScore]) -> Report:
    return Report(cases=tuple(cases))


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None
