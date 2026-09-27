"""Calibration arithmetic behind E's metric — no I/O."""

from __future__ import annotations

import pytest

from autune_intelligence.calibration import (
    brier,
    calibration_report,
    log_loss,
    reliability_bins,
)


def test_brier_is_zero_for_perfect_confident_predictions() -> None:
    assert brier([1.0, 0.0], [True, False]) == 0.0
    assert brier([0.0, 1.0], [True, False]) == 1.0


def test_log_loss_clips_rather_than_diverging() -> None:
    assert log_loss([0.0], [True]) == pytest.approx(34.5, rel=0.01)


def test_bins_group_by_predicted_probability_and_skip_empty_ones() -> None:
    bins = reliability_bins([0.05, 0.15, 0.12, 1.0], [False, True, False, True])

    assert [(b.lower, b.count) for b in bins] == [(0.0, 1), (0.1, 2), (0.9, 1)]
    assert bins[1].observed_rate == 0.5
    assert bins[2].mean_predicted == 1.0  # p == 1.0 lands in the top bin


def test_a_calibrated_constant_has_zero_ece_and_zero_skill() -> None:
    outcomes = [True] * 3 + [False] * 7

    report = calibration_report([0.3] * 10, outcomes)

    assert report.base_rate == 0.3
    assert report.ece == pytest.approx(0.0)
    assert report.brier_skill == pytest.approx(0.0)


def test_skill_is_positive_when_the_model_separates_outcomes() -> None:
    report = calibration_report([0.9, 0.8, 0.1, 0.2], [True, True, False, False])

    assert report.brier_skill is not None and report.brier_skill > 0.5


def test_skill_is_undefined_when_every_outcome_is_the_same() -> None:
    assert calibration_report([0.2, 0.3], [False, False]).brier_skill is None


def test_empty_input_is_an_error_not_a_zero() -> None:
    with pytest.raises(ValueError):
        calibration_report([], [])
