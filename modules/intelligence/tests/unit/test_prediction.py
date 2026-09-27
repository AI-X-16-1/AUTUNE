"""Misalignment features, the #27 display gate, and the heuristic baseline — no I/O."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from autune_contracts import ContextLinks, ExtractionResult, GapReport
from autune_intelligence.pipeline.predictor import HeuristicMisalignmentPredictor
from autune_intelligence.prediction import (
    MeetingFeatures,
    meeting_features,
    prediction_visible,
)

NOW = datetime(2026, 9, 27, tzinfo=UTC)


def _change(i: int, change_type: str, absent: list[str] | None = None) -> dict:
    return {
        "thread_id": f"thr_{i}",
        "source_decision_id": f"dec_{i}",
        "current_statement": "s",
        "change_type": change_type,
        "confidence": 0.9,
        "key_stakeholders_absent": absent or [],
    }


def _features(**kw: object) -> MeetingFeatures:
    return meeting_features(
        quality_value=kw.pop("quality_value", 0.7),  # type: ignore[arg-type]
        extraction=kw.pop("extraction", None),  # type: ignore[arg-type]
        gap=kw.pop("gap", None),  # type: ignore[arg-type]
        context=kw.pop("context", None),  # type: ignore[arg-type]
        alignment_scores=kw.pop("alignment_scores", []),  # type: ignore[arg-type]
        missing_source_count=kw.pop("missing_source_count", 0),  # type: ignore[arg-type]
    )


# --- features ------------------------------------------------------------


def test_missing_sources_leave_their_features_unmeasured() -> None:
    f = _features(missing_source_count=3)

    assert f.decision_count is None and f.high_gap_count is None
    assert f.changed_decision_share is None and f.reversed_count is None
    assert f.change_with_stakeholder_absent is None and f.alignment_min is None
    assert f.missing_source_count == 3


def test_lineage_features_count_moved_decisions_but_not_new_ones() -> None:
    context = ContextLinks(
        meeting_id="mtg_1",
        decision_lineage=[
            _change(1, "new"),
            _change(2, "unchanged"),
            _change(3, "modified", absent=["usr_x"]),
            _change(4, "reversed"),
        ],
    )

    f = _features(context=context)

    assert f.changed_decision_share == pytest.approx(2 / 3)
    assert f.reversed_count == 1
    assert f.change_with_stakeholder_absent is True


def test_alignment_feature_is_the_weakest_pair() -> None:
    f = _features(alignment_scores=[0.9, 0.3])

    assert f.alignment_min == 0.3


def test_extraction_and_gap_counts() -> None:
    extraction = ExtractionResult(
        meeting_id="mtg_1",
        decisions=[{"id": "dec_1", "statement": "s", "confidence": 0.9}],
        action_items=[
            {"id": "act_1", "description": "d", "confidence": 0.9},
            {"id": "act_2", "description": "d", "status": "todo", "confidence": 0.9},
        ],
        ambiguous_agreements=[{"utterance_id": "utt_1", "reason": "r"}],
    )
    gap = GapReport(
        meeting_id="mtg_1",
        gaps=[
            {"id": "gap_1", "category": "c", "title": "t", "severity": "high", "risk_score": 0.9},
            {"id": "gap_2", "category": "c", "title": "t", "severity": "low", "risk_score": 0.1},
        ],
    )

    f = _features(extraction=extraction, gap=gap)

    assert (f.decision_count, f.high_gap_count, f.gap_count) == (1, 1, 2)
    assert f.ambiguous_agreement_count == 1
    assert f.unconfirmed_action_share == 0.5  # the default status needs confirmation


# --- #27 gate --------------------------------------------------------------


def test_gate_needs_both_four_weeks_and_three_meetings() -> None:
    four_weeks_ago = NOW - timedelta(weeks=4)

    assert prediction_visible(four_weeks_ago, 3, now=NOW)
    assert not prediction_visible(four_weeks_ago, 2, now=NOW)
    assert not prediction_visible(NOW - timedelta(weeks=3, days=6), 10, now=NOW)
    assert not prediction_visible(None, 0, now=NOW)


# --- heuristic baseline ----------------------------------------------------


def test_heuristic_probabilities_are_in_the_unit_interval() -> None:
    (p,) = HeuristicMisalignmentPredictor().predict([_features()])

    assert 0.0 < p < 1.0


def test_heuristic_rises_with_role_disagreement() -> None:
    base = _features(alignment_scores=[0.9])
    worse = replace(base, alignment_min=0.2)

    low, high = HeuristicMisalignmentPredictor().predict([base, worse])

    assert high > low


def test_heuristic_rises_when_the_team_is_already_reversing_itself() -> None:
    base = _features()
    reversing = replace(
        base, changed_decision_share=1.0, reversed_count=2, change_with_stakeholder_absent=True
    )

    low, high = HeuristicMisalignmentPredictor().predict([base, reversing])

    assert high > low


def test_heuristic_count_caps_bound_the_effect_of_meeting_length() -> None:
    base = _features()
    many, huge = replace(base, high_gap_count=5), replace(base, high_gap_count=500)

    a, b = HeuristicMisalignmentPredictor().predict([many, huge])

    assert a == b
