"""The committed evaluation set, and the rules a labeled case has to obey.

No database and no model: these are facts about the JSON and the templates it
refers to. A label naming an item no template defines, or a template item no
label covers, is caught here rather than by a precision figure that quietly
measured something else.
"""

from __future__ import annotations

import json
from importlib.resources import files

import pytest

from autune_gap.eval.dataset import DEFAULT_DATASET, EvalSetError, _parse_case, load_cases
from autune_gap.template import get_template

# --- the set that ships -----------------------------------------------------


def test_the_committed_set_loads() -> None:
    cases = load_cases()

    assert cases
    assert len({case.id for case in cases}) == len(cases)


def test_every_case_labels_its_whole_template() -> None:
    """Closed world. Without it "raised and not in `real_gaps`" cannot be read
    as a false positive — it might be an item nobody labeled — and the
    precision figure would be measuring the labeler's diligence.

    `load_cases` already enforces this; asserting it here says which rule the
    loader's error is about.
    """
    for case in load_cases():
        items = {item.key for item in get_template(case.template_key).items}

        assert case.real_gaps | case.settled == items, case.id
        assert not case.real_gaps & case.settled, case.id


def test_the_set_has_a_case_with_no_real_gaps_and_one_with_nothing_but() -> None:
    """The two ends the metric needs. A meeting that settled everything makes
    any gap a false positive, which is the most direct thing precision can be
    handed; a meeting that settled nothing is what recall is read off."""
    cases = load_cases()

    assert any(not case.real_gaps for case in cases)
    assert any(not case.settled for case in cases)


def test_both_templates_are_exercised() -> None:
    """A keyword edit that helps one template and breaks the other should show
    up, and it cannot if the set only ever runs one of them."""
    templates = {case.template_key for case in load_cases()}

    assert templates == {"general", "feature_planning"}


def test_every_case_has_speakers_and_lines() -> None:
    for case in load_cases():
        assert case.lines, case.id
        assert case.speakers, case.id
        assert all(line.text.strip() for line in case.lines), case.id


# --- what the loader refuses ------------------------------------------------


def raw(**overrides: object) -> dict[str, object]:
    settled = [item.key for item in get_template("general").items]
    case: dict[str, object] = {
        "id": "c1",
        "template": "general",
        "lines": [{"speaker": "화자", "text": "본문"}],
        "real_gaps": [],
        "settled": settled,
    }
    return case | overrides


def test_a_valid_case_parses() -> None:
    assert _parse_case(raw(), DEFAULT_DATASET).id == "c1"


def test_a_label_naming_an_item_no_template_defines_is_refused() -> None:
    """The typo case: `owner` for `ownership` would otherwise leave `ownership`
    unlabeled and count every gap raised on it as a false positive."""
    with pytest.raises(EvalSetError, match="does not"):
        _parse_case(raw(real_gaps=["owner"]), DEFAULT_DATASET)


def test_an_item_labeled_both_ways_is_refused() -> None:
    with pytest.raises(EvalSetError, match="twice"):
        _parse_case(raw(real_gaps=["risk"]), DEFAULT_DATASET)


def test_leaving_an_item_unlabeled_is_refused() -> None:
    with pytest.raises(EvalSetError, match="unlabeled"):
        _parse_case(raw(settled=["risk"]), DEFAULT_DATASET)


def test_an_unknown_template_is_refused_as_an_eval_set_error() -> None:
    """Reraised as this module's error, so the CLI reports a bad evaluation set
    and exits 2 instead of showing a traceback for a typo in a JSON field."""
    with pytest.raises(EvalSetError, match="unknown template"):
        _parse_case(raw(template="retrospective"), DEFAULT_DATASET)


def test_the_committed_set_declares_the_version_the_loader_accepts() -> None:
    """The loader refuses any other value, so a set written against a later
    schema cannot parse into the wrong shape silently."""
    document = json.loads(
        (files("autune_gap.eval") / "fixtures" / DEFAULT_DATASET).read_text(encoding="utf-8")
    )

    assert document["version"] == 1
    assert document["notes"], "the set has to say what it is and is not"
