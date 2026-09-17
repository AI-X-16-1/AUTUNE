"""Our MER and PIER against numbers produced by HiKE's own scoring.

``fixtures/hike_fidelity.json`` holds real corpus rows and real large-v3
hypotheses whose scores were computed by a port of HiKE's pipeline (see
``scripts/hike_fidelity_fixture.py``). If a change here makes these drift,
the number stops being comparable to the paper's table, whatever the unit
tests say.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autune_audio.eval.codeswitch import mixed_error_rate, point_of_interest_error_rate

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "hike_fidelity.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("row", FIXTURE["rows"], ids=[r["sample_id"][:8] for r in FIXTURE["rows"]])
def test_mer_and_pier_reproduce_hike_scoring(row: dict[str, object]) -> None:
    loanwords = tuple((k, e) for k, e in row["loanwords"])  # type: ignore[union-attr]
    hypothesis = str(row["hypothesis"])

    mer = mixed_error_rate(str(row["text_normalized"]), hypothesis, loanwords=loanwords)
    pier = point_of_interest_error_rate(
        str(row["text_pier_labeled"]), hypothesis, loanwords=loanwords
    )

    assert mer.mer == pytest.approx(row["mer"], abs=1e-9)
    assert pier.pier == pytest.approx(row["pier"], abs=1e-9)


def test_the_fixture_covers_what_separates_the_two_normalisers() -> None:
    covered = {feature for row in FIXTURE["rows"] for feature in row["features"]}
    assert {"contraction", "hyphen", "digit", "loanword", "perfect"} <= covered
    assert {row["cs_level"] for row in FIXTURE["rows"]} == {"word", "phrase", "sentence"}


def test_the_divergence_from_hike_is_small_and_accounted_for() -> None:
    """Every candidate that disagreed with the port is one of the two documented
    kinds. A third kind appearing here means a new fidelity bug."""
    total, agreeing = FIXTURE["candidates_scored"], FIXTURE["candidates_agreeing"]
    explained = (
        FIXTURE["candidates_differing_by_tie_break_only"]
        + FIXTURE["candidates_differing_by_capitalised_loanword_label"]
    )
    assert total - agreeing == explained
    assert agreeing / total > 0.98
