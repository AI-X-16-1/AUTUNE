"""The before/after report ``python -m autune_gap.eval --compare`` prints.

Built from hand-made scores, so each test names the movement it is about. The
report is what somebody quotes in a PR, and a line that says a false positive
closed when it merely moved band is the kind of claim it exists to get right.
"""

from __future__ import annotations

from autune_gap.eval.__main__ import format_comparison
from autune_gap.eval.metrics import NO_NOUN, CaseScore, score


def case(
    raised_high: set[str],
    raised_any: set[str] | None = None,
    *,
    real: set[str] | None = None,
    causes: dict[str, str] | None = None,
) -> CaseScore:
    return CaseScore(
        case_id="retro",
        template_key="general",
        real=frozenset(real if real is not None else {"risk"}),
        raised_high=frozenset(raised_high),
        raised_any=frozenset(raised_any if raised_any is not None else raised_high),
        raised_partial=frozenset(),
        topics=10,
        fp_cause=causes if causes is not None else {},
    )


def test_a_false_positive_that_left_the_high_band_is_closed_below_high() -> None:
    before = score([case({"risk", "ownership"}, causes={"ownership": NO_NOUN})])
    after = score([case({"risk"}, {"risk", "ownership"})])

    report = format_comparison(before, after, embedder="local")

    assert "ownership" in report
    assert "closed -- still raised, below high" in report


def test_a_false_positive_no_longer_raised_is_closed() -> None:
    before = score([case({"risk", "ownership"}, causes={"ownership": NO_NOUN})])
    after = score([case({"risk"})])

    assert "closed -- no longer raised" in format_comparison(before, after, embedder="local")


def test_a_false_positive_still_raised_says_so_with_its_cause() -> None:
    before = score([case({"risk", "ownership"}, causes={"ownership": NO_NOUN})])
    after = score([case({"risk", "ownership"}, causes={"ownership": NO_NOUN})])

    assert f"still raised ({NO_NOUN})" in format_comparison(before, after, embedder="local")


def test_a_real_gap_the_candidate_stopped_surfacing_is_the_listed_price() -> None:
    before = score([case({"risk"})])
    after = score([case(set(), {"risk"})])

    report = format_comparison(before, after, embedder="local")

    assert "real gaps no longer surfaced: ['retro:risk']" in report


def test_both_headline_numbers_are_printed_side_by_side() -> None:
    before = score([case({"risk", "ownership"}, causes={"ownership": NO_NOUN})])
    after = score([case({"risk"})])

    report = format_comparison(before, after, embedder="local")

    assert "precision (high)" in report
    assert "0.5000" in report
    assert "1.0000" in report
