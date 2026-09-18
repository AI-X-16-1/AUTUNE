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

from dataclasses import dataclass

TARGET_PRECISION_SIX_WEEKS = 0.70
TARGET_PRECISION_THREE_MONTHS = 0.82
"""docs/product/prd.md section 12, and docs/modules/gap.md, "Metric"."""


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
        """``partial`` or ``missing`` — which rule raised this one."""
        return "partial" if item_key in self.raised_partial else "missing"


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
        counts = {"missing": 0, "partial": 0}
        for case in self.cases:
            for item in case.false_positives:
                counts[case.cause(item)] += 1
        return counts

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
