"""Scoring for the live-speaker threshold sweep, on vectors with known answers."""

from __future__ import annotations

import numpy as np
import pytest

from autune_audio.eval.speakers import (
    Block,
    completeness,
    parse_reference,
    purity,
    reference_speaker,
    simulate,
    sweep,
)


def basis(i: int, dim: int = 4) -> np.ndarray:
    v = np.zeros(dim, dtype=np.float32)
    v[i] = 1.0
    return v


def test_a_reference_parses_into_blocks() -> None:
    assert parse_reference("1.5-39.7:A, 40.0-73.7:B") == (
        Block(1.5, 39.7, "A"),
        Block(40.0, 73.7, "B"),
    )


def test_the_midpoint_decides_the_reference_speaker() -> None:
    blocks = parse_reference("0-10:A,10-20:B")
    assert reference_speaker(blocks, 8.0, 12.0) == "B"  # midpoint 10.0 starts B's block
    assert reference_speaker(blocks, 7.0, 11.0) == "A"  # midpoint 9.0
    assert reference_speaker(blocks, 25.0, 30.0) is None


def test_simulate_replays_the_tracker_in_order() -> None:
    vectors = [basis(0), basis(1), basis(0)]
    assert simulate(
        vectors, [3.0, 3.0, 3.0], threshold=0.6, max_speakers=None, min_seconds=1.0
    ) == [
        "화자 1",
        "화자 2",
        "화자 1",
    ]
    assert simulate(vectors, [3.0, 3.0, 3.0], threshold=0.6, max_speakers=1, min_seconds=1.0) == [
        "화자 1",
        "화자 1",
        "화자 1",
    ]


def test_simulate_honours_min_seconds() -> None:
    vectors = [basis(0), basis(1)]
    # A 0.5 s second utterance may not open a cluster at min_seconds=1.0 …
    assert simulate(vectors, [3.0, 0.5], threshold=0.6, max_speakers=None, min_seconds=1.0) == [
        "화자 1",
        "화자 1",
    ]
    # … and may at min_seconds=0.2.
    assert simulate(vectors, [3.0, 0.5], threshold=0.6, max_speakers=None, min_seconds=0.2) == [
        "화자 1",
        "화자 2",
    ]


def test_purity_and_completeness_on_a_perfect_labelling() -> None:
    labels = ["화자 1", "화자 2", "화자 1"]
    truth = ["A", "B", "A"]
    assert purity(labels, truth) == 1.0
    assert completeness(labels, truth) == 1.0


def test_purity_charges_a_mixed_cluster_and_completeness_charges_a_split_speaker() -> None:
    # One cluster holds A, A, B: purity over the three utterances = 2/3.
    assert purity(["c", "c", "c"], ["A", "A", "B"]) == pytest.approx(2 / 3)
    # A is split across two clusters 2:1, B is whole: completeness = (2/3 + 1) / 2.
    assert completeness(["c1", "c1", "c2", "c3"], ["A", "A", "A", "B"]) == pytest.approx(
        (2 / 3 + 1.0) / 2
    )


def test_unscored_utterances_are_ignored() -> None:
    assert purity(["c", "c"], ["A", None]) == 1.0
    assert completeness(["c", "d"], ["A", None]) == 1.0


def test_sweep_reports_every_threshold_capped_and_not() -> None:
    vectors = [basis(0), basis(1), basis(0), basis(1)]
    seconds = [3.0] * 4
    truth = ["A", "B", "A", "B"]
    scores = sweep(vectors, seconds, truth, thresholds=[0.5, 0.9], max_speakers=2, min_seconds=1.0)
    assert [(s.threshold, s.capped) for s in scores] == [
        (0.5, False),
        (0.5, True),
        (0.9, False),
        (0.9, True),
    ]
    assert all(s.clusters == 2 and s.purity == 1.0 and s.completeness == 1.0 for s in scores)
