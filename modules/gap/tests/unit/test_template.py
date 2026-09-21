"""The domain templates module C ships, and the rules a template file must obey.

No database. Templates are files in the package (#22), so everything here is a
fact about what was checked in — a bad template is caught in the unit run rather
than by a meeting producing a strange report.
"""

from __future__ import annotations

import pytest

from autune_core.errors import ConfigurationError, ValidationError
from autune_gap import template

# --- what is shipped --------------------------------------------------------


def test_both_mvp_templates_load() -> None:
    """#22 settles on two for the MVP, and says why: the metric is precision
    0.70+ and the validation sample is five to ten real meetings in W5. Five
    templates would split that sample one or two meetings deep and precision
    could not be measured at all."""
    keys = set(template.load_templates())

    assert keys == {"general", "feature_planning"}


def test_feature_planning_extends_general() -> None:
    """The five items any decision meeting has to settle, then the five a
    feature meeting adds. Inherited items come first so the general checklist
    reads in the same order whichever template is in force."""
    general = template.get_template("general")
    planning = template.get_template("feature_planning")

    assert len(general.items) == 5
    assert len(planning.items) == 10
    assert [item.key for item in planning.items][:5] == [item.key for item in general.items]


def test_an_extending_template_names_both_files_in_its_version() -> None:
    """A template that extends another changes when its parent changes.

    One number could not say that, and a gap raised before an edit to
    ``general`` would then be averaged together with one raised after it — the
    thing ``template_version`` exists to keep apart.
    """
    assert template.get_template("general").version == "general.1"
    assert template.get_template("feature_planning").version == "general.1+feature_planning.1"


def test_every_shipped_item_can_raise_a_usable_gap() -> None:
    """An item with no question closes nothing, and one weighted outside (0, 1]
    would put a risk score outside the range ``gap_gaps`` accepts."""
    for one in template.available():
        for item in one.items:
            assert item.question.strip(), f"{one.key}.{item.key}"
            assert item.item.strip(), f"{one.key}.{item.key}"
            assert 0 < item.weight <= 1, f"{one.key}.{item.key}"
            assert item.keywords, f"{one.key}.{item.key}"


def test_item_keys_are_unique_within_a_template() -> None:
    """``(meeting_id, template_key, template_item_key)`` is a gap's identity
    across re-runs. Two items sharing a key would give two gaps one identity,
    and the second would overwrite the first every pass."""
    for one in template.available():
        keys = [item.key for item in one.items]
        assert len(keys) == len(set(keys)), one.key


# --- the rules a template file has to obey ----------------------------------


def test_an_unknown_template_key_is_a_422_naming_what_exists() -> None:
    """The key arrives from a request body or a stored override, so what is
    wrong is the value and not the address."""
    with pytest.raises(ValidationError) as raised:
        template.get_template("retrospective")

    assert "general" in str(raised.value)


def test_a_keyword_shorter_than_two_characters_is_refused() -> None:
    """It would match by accident, in every meeting, and the item behind it
    would then never raise a gap at all."""
    with pytest.raises(ConfigurationError, match="shorter than"):
        template._item(
            {
                "key": "k",
                "category": "c",
                "item": "i",
                "weight": 0.5,
                "question": "q?",
                "keywords": ["성능", "A"],
            },
            "test",
        )


def test_an_item_with_no_keywords_is_refused() -> None:
    """Nothing could ever match it, so it would raise its gap in every meeting
    forever — the worst shape a precision metric can be handed."""
    with pytest.raises(ConfigurationError, match="no keywords"):
        template._item(
            {
                "key": "k",
                "category": "c",
                "item": "i",
                "weight": 0.5,
                "question": "q?",
                "keywords": [],
            },
            "test",
        )


def test_a_weight_outside_the_unit_range_is_refused() -> None:
    """``gap_gaps.risk_score`` has a check constraint, and the weight is one of
    the inputs the score is a weighted mean of."""
    with pytest.raises(ConfigurationError, match="outside"):
        template._item(
            {
                "key": "k",
                "category": "c",
                "item": "i",
                "weight": 1.5,
                "question": "q?",
                "keywords": ["성능"],
            },
            "test",
        )


def test_a_template_may_not_redefine_an_inherited_item() -> None:
    """Two items with one key is the identity clash above, arriving through
    inheritance where it is harder to see."""
    raw = {
        "parent": {"key": "parent", "name": "p", "version": 1, "items": [_raw("shared")]},
        "child": {
            "key": "child",
            "name": "c",
            "version": 1,
            "extends": "parent",
            "items": [_raw("shared")],
        },
    }

    with pytest.raises(ConfigurationError, match="redefines inherited"):
        template._resolve("child", raw, seen=())


def test_a_template_that_extends_itself_is_refused_with_the_chain() -> None:
    """Otherwise the recursion is a stack overflow with nothing in it to read."""
    raw = {
        "a": {"key": "a", "name": "a", "version": 1, "extends": "b", "items": []},
        "b": {"key": "b", "name": "b", "version": 1, "extends": "a", "items": []},
    }

    with pytest.raises(ConfigurationError, match="extends itself"):
        template._resolve("a", raw, seen=())


def test_extending_a_template_that_does_not_exist_is_refused() -> None:
    raw = {"a": {"key": "a", "name": "a", "version": 1, "extends": "missing", "items": []}}

    with pytest.raises(ConfigurationError, match="unknown"):
        template._resolve("a", raw, seen=())


def _raw(key: str) -> dict[str, object]:
    return {
        "key": key,
        "category": "measurement",
        "item": "항목",
        "weight": 0.5,
        "question": "질문?",
        "keywords": ["성능"],
    }
