"""The metric glossary: Korean passages whose numbers come from code (spec section 5)."""

from __future__ import annotations

import re

import pytest

from autune_intelligence import glossary, service


def test_every_placeholder_is_filled() -> None:
    for p in glossary.passages():
        assert "{" not in p.text and "}" not in p.text, p.key


def test_constants_print_as_people_read_them() -> None:
    """A timedelta or a tuple must read as a person says it (Review Focus 2)."""
    assert glossary.CONSTANTS["action.window"] == "4주"
    assert glossary.CONSTANTS["action.min_meetings"] == "3건"
    assert glossary.CONSTANTS["weight.decision_density"] == "30%"
    assert glossary.CONSTANTS["grade.cutoffs"].startswith("A 0.9 이상")
    assert glossary.CONSTANTS["weekly.active_within"] == "13주"
    assert glossary.CONSTANTS["report.correction_window"] == "5분"
    assert glossary.CONSTANTS["weekly.default_weekday"] == "월요일"
    assert glossary.CONSTANTS["prediction.horizon"] == "14일"
    assert glossary.CONSTANTS["prediction.min_meetings"] == "3건"


def test_the_weights_quoted_are_the_weights_in_code() -> None:
    quality = next(p for p in glossary.passages() if p.key == "quality.weights")
    for key, weight in service.WEIGHTS.items():
        assert f"{weight:.0%}" in quality.text, key


def test_passages_cover_every_area_with_unique_keys() -> None:
    keys = [p.key for p in glossary.passages()]
    assert len(keys) == len(set(keys))
    for area in ("quality.", "gaps.", "alignment.", "prediction.", "actions.", "reports."):
        assert any(k.startswith(area) for k in keys), area
    assert 20 <= len(keys) <= 40


def test_a_passage_is_short_enough_for_the_budget() -> None:
    for p in glossary.passages():
        assert len(p.text) <= 400, p.key
        assert re.search(r"[가-힣]", p.text), p.key  # user-facing text is Korean


def test_an_unknown_placeholder_fails_loudly() -> None:
    with pytest.raises(KeyError):
        glossary.fill("{no.such}")


def test_a_passage_renders_its_constant() -> None:
    window = next(p for p in glossary.passages() if p.key == "actions.window")
    assert glossary.CONSTANTS["action.window"] in window.text
