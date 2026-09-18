"""``service._best_f1_threshold`` -- pure, no database. See issue #256."""

from __future__ import annotations

from autune_context.service import _best_f1_threshold


def test_a_clean_separation_lands_between_the_two_clusters():
    positive = [0.9, 0.85, 0.8]
    negative = [0.4, 0.3, 0.2]

    threshold = _best_f1_threshold(positive, negative, low=0.0, high=1.0)

    assert 0.4 < threshold <= 0.8


def test_no_confirmed_links_yet_falls_back_to_the_ceiling():
    # Nothing to assert on with confidence -- most conservative answer.
    threshold = _best_f1_threshold([], [0.5, 0.6, 0.7], low=0.3, high=0.9)

    assert threshold == 0.9


def test_no_rejected_links_yet_still_returns_a_threshold_that_keeps_every_confirm():
    threshold = _best_f1_threshold([0.5, 0.6, 0.7], [], low=0.3, high=0.9)

    assert threshold <= 0.5


def test_result_is_always_clipped_to_the_configured_range():
    # Every score is well above a degenerate default range -- the search must
    # not wander outside [low, high] chasing a perfect (but untrustworthy) fit.
    threshold = _best_f1_threshold([0.99], [0.98], low=0.3, high=0.9)

    assert 0.3 <= threshold <= 0.9


def test_a_tie_at_the_worst_possible_f1_keeps_the_highest_cutoff():
    # With nothing confirmed, every cutoff scores f1=0.0 -- a full tie. The
    # highest cutoff (== high) should win, per the "ties keep the highest"
    # rule -- this is the same case test_no_confirmed_links_yet_falls_back_to_
    # the_ceiling checks, from the tie-breaking angle rather than the
    # no-confirmed-links angle.
    threshold = _best_f1_threshold([], [0.5, 0.6], low=0.0, high=1.0)

    assert threshold == 1.0
