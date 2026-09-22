"""Scoring for the evaluation harness. Pure sets, no database and no model.

The rules worth pinning are the ones about *not* reporting a number: precision
over nothing raised, recall over nothing labeled, and the band the metric is
measured on.
"""

from __future__ import annotations

from autune_gap.eval.metrics import (
    EXTRACTION,
    KEYWORD,
    NO_NOUN,
    PARTIAL,
    TARGET_PRECISION_SIX_WEEKS,
    CaseScore,
    classify_false_positive,
    score,
)


def case(
    case_id: str = "c1",
    *,
    real: set[str] | None = None,
    high: set[str] | None = None,
    any_severity: set[str] | None = None,
    partial: set[str] | None = None,
    causes: dict[str, str] | None = None,
    topics: int = 5,
) -> CaseScore:
    raised_high = frozenset(high or ())
    return CaseScore(
        case_id=case_id,
        template_key="general",
        real=frozenset(real or ()),
        raised_high=raised_high,
        raised_any=frozenset(any_severity) if any_severity is not None else raised_high,
        raised_partial=frozenset(partial or ()),
        topics=topics,
        fp_cause=dict(causes or {}),
    )


# --- what precision is measured over ----------------------------------------


def test_precision_is_the_surfaced_band() -> None:
    """Only ``high`` reaches a reader by default, so a ``medium`` false
    positive is not a false statement to anybody yet."""
    report = score([case(real={"risk"}, high={"risk"}, any_severity={"risk", "next_step"})])

    assert report.precision == 1.0
    assert report.precision_any_severity == 0.5


def test_a_gap_raised_on_a_settled_item_is_a_false_positive() -> None:
    report = score([case(real={"risk"}, high={"risk", "ownership"})])

    assert report.precision == 0.5
    assert report.cases[0].false_positives == frozenset({"ownership"})
    assert report.cases[0].true_positives == frozenset({"risk"})


def test_precision_pools_across_cases_rather_than_averaging_them() -> None:
    """A case with one gap and a case with nine should not weigh the same. The
    metric is "of the gaps we showed, how many were real", and that is one
    ratio over the whole run."""
    report = score(
        [
            case("small", real={"risk"}, high={"risk"}),
            case("large", real=set(), high={"a", "b", "c"}),
        ]
    )

    assert report.precision == 0.25


# --- what is not reported ---------------------------------------------------


def test_precision_over_nothing_raised_is_not_measured() -> None:
    """Not 0.0 and not 1.0 — both are a claim about a pipeline that said
    nothing. Same convention as ``detect.score`` for a signal it cannot read."""
    report = score([case(real={"risk"}, high=set())])

    assert report.precision is None
    assert report.meets_six_week_target is False


def test_recall_over_a_set_that_labels_no_real_gap_is_not_measured() -> None:
    """A set of meetings that settled everything is a legitimate thing to score
    precision on, and recall means nothing over it."""
    report = score([case(real=set(), high=set())])

    assert report.recall is None


def test_recall_is_reported_when_there_is_something_to_recall() -> None:
    report = score([case(real={"risk", "ownership"}, high={"risk"})])

    assert report.recall == 0.5
    assert report.cases[0].missed == frozenset({"ownership"})


# --- the target -------------------------------------------------------------


def test_the_target_is_inclusive() -> None:
    """A run landing exactly on the published figure has met it."""
    report = score(
        [case(real={"a", "b", "c", "d", "e", "f", "g"}, high={"a", "b", "c", "d", "e", "f", "g"})]
    )
    assert report.precision == 1.0
    assert report.meets_six_week_target

    on_target = score([case(real={"a", "b"}, high={"a", "b", "c"})])
    assert on_target.precision is not None
    assert (on_target.precision >= TARGET_PRECISION_SIX_WEEKS) is on_target.meets_six_week_target


# --- what a bad number should be read against -------------------------------


def test_cases_with_an_empty_graph_are_called_out() -> None:
    """They raise nothing by design, so they pull recall down for a reason that
    is about extraction rather than about template comparison. Worth seeing
    rather than silently averaging over."""
    report = score(
        [
            case("found-nothing", real={"risk"}, high=set(), topics=0),
            case("found-something", real={"risk"}, high={"risk"}, topics=7),
        ]
    )

    assert [c.case_id for c in report.empty_graph_cases] == ["found-nothing"]


# --- which rule a false positive came from ----------------------------------


def classify(
    *,
    partial: bool = False,
    expected: tuple[str, ...] = ("리스크",),
    labels: set[str] | None = None,
    keywords: tuple[str, ...] = ("위험",),
) -> str:
    return classify_false_positive(
        partial=partial,
        expected=expected,
        topic_labels=frozenset(labels if labels is not None else set()),
        keywords=keywords,
    )


def test_a_threshold_decision_is_answered_before_anything_else() -> None:
    """A partial finding says nothing about keywords or extraction — the
    meeting named the thing and the centrality threshold decided that was not
    enough."""
    assert classify(partial=True, expected=(), labels=set()) == PARTIAL


def test_an_item_no_noun_names_is_out_of_reach() -> None:
    """Empty evidence is a claim: the meeting settled it with a verb or a date.
    Matching keywords against topic labels is lexical and what settled the item
    is grammatical, so neither half of this module gets to it."""
    assert classify(expected=(), labels={"정렬 로직"}) == NO_NOUN


def test_a_noun_that_never_became_a_topic_is_a_step_one_problem() -> None:
    assert classify(expected=("리스크",), labels={"외부 전송"}) == EXTRACTION


def test_a_noun_in_the_graph_the_keywords_do_not_name_is_a_template_problem() -> None:
    assert classify(expected=("리스크",), labels={"리스크 예외"}) == KEYWORD


def test_a_truncated_label_is_extraction_and_not_a_keyword_miss() -> None:
    """The subtle one, and the reason the match is one-directional.

    The meeting said "외부 전송 실패인데"; extraction dropped the
    particle-carrying tail and stored "외부 전송". Reading a label *inside* the
    expected term as a match — which is what ``detect.match`` does — answered
    ``KEYWORD`` for a step-1 truncation, and would have sent somebody to widen a
    keyword list over it.
    """
    assert classify(expected=("외부 전송 실패",), labels={"외부 전송"}) == EXTRACTION


def test_causes_are_counted_across_the_run_and_empty_buckets_are_dropped() -> None:
    report = score(
        [
            case(
                "a",
                real=set(),
                high={"ownership", "risk"},
                causes={"ownership": NO_NOUN, "risk": EXTRACTION},
            ),
            case("b", real=set(), high={"dependency"}, causes={"dependency": NO_NOUN}),
        ]
    )

    assert report.false_positives_by_cause == {EXTRACTION: 1, NO_NOUN: 2}


def test_only_the_reachable_causes_are_counted_as_fixable() -> None:
    """A precision figure says how far off the target is; this says how much of
    the distance is even reachable from this module."""
    report = score(
        [
            case(
                real=set(),
                high={"a", "b", "c", "d"},
                causes={"a": EXTRACTION, "b": KEYWORD, "c": PARTIAL, "d": NO_NOUN},
            )
        ]
    )

    assert report.fixable_false_positives == 3


def test_a_case_with_no_evidence_labels_reports_unclassified() -> None:
    """The split is a claim about labeled data and is not inferred for a case
    nobody labeled."""
    scored = case(real=set(), high={"ownership"})

    assert scored.cause("ownership") == "unclassified"
    assert score([scored]).fixable_false_positives == 0


def test_a_true_positive_is_not_counted_by_cause() -> None:
    """The split explains what the metric is losing, not what it got right."""
    report = score([case(real={"risk"}, high={"risk"}, causes={})])

    assert report.false_positives_by_cause == {}
