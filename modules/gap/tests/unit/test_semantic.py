"""Reading the speech by meaning — ``semantic``.

The vectors here are written by hand, so each test names the geometry it is
about instead of depending on what KURE-v1 happens to say. What the real model
does on the eval set is ``python -m autune_gap.eval --compare``'s job.
"""

from __future__ import annotations

import pytest

from autune_gap import semantic, template
from autune_gap.eval.dataset import DEFAULT_DATASET, load_cases
from autune_gap.pipeline import FakeEmbedder

OWNER = [1.0, 0.0, 0.0]
DEPENDENCY = [0.0, 1.0, 0.0]
NOTHING = [0.0, 0.0, 1.0]

EXAMPLES = [OWNER, DEPENDENCY, NOTHING]
LABELS = ["ownership", "dependency", semantic.BACKGROUND_KEY]


def unit(*values: float) -> list[float]:
    norm = sum(value * value for value in values) ** 0.5
    return [value / norm for value in values]


def heard(*utterances: list[float], floor: float = 0.55, margin: float = 0.0) -> frozenset[str]:
    return semantic.heard(list(utterances), EXAMPLES, LABELS, floor=floor, margin=margin)


def test_an_utterance_counts_for_the_item_it_is_nearest_to() -> None:
    assert heard(unit(0.9, 0.3, 0.1)) == {"ownership"}


def test_an_utterance_nearest_to_the_background_counts_for_nothing() -> None:
    """The reason the background class exists: "네 알겠습니다, 그럼 마치겠습니다"
    is close to "제가 맡겠습니다" on the ending alone, and closer still to
    sentences that settle nothing."""
    assert heard(unit(0.6, 0.0, 0.8)) == frozenset()


def test_an_utterance_below_the_floor_counts_for_nothing() -> None:
    """Nearest to an item, but not near it. Every utterance is nearest to
    something, and most of a meeting settles nothing on the checklist."""
    assert heard(unit(0.5, 0.45, 0.45), floor=0.8) == frozenset()


def test_a_margin_makes_a_near_tie_count_for_neither() -> None:
    close_to_both = unit(0.7, 0.68, 0.1)

    assert heard(close_to_both, margin=0.0) == {"ownership"}
    assert heard(close_to_both, margin=0.05) == frozenset()


def test_one_utterance_counts_for_one_item() -> None:
    """Letting an utterance count for every class above the floor would bring
    back the shared-ending problem the nearest-class rule is there to avoid."""
    assert len(heard(unit(0.7, 0.69, 0.0))) == 1


def test_every_utterance_is_read() -> None:
    assert heard(unit(1.0, 0.1, 0.0), unit(0.1, 1.0, 0.0)) == {"ownership", "dependency"}


def test_no_speech_hears_nothing() -> None:
    assert semantic.heard([], EXAMPLES, LABELS, floor=0.5, margin=0.0) == frozenset()


def test_vectors_and_labels_must_line_up() -> None:
    with pytest.raises(ValueError, match="labels"):
        semantic.heard([OWNER], EXAMPLES, LABELS[:2], floor=0.5, margin=0.0)


def test_a_single_class_is_refused() -> None:
    """With one class, "nearest" would mean "above the floor" and nothing would
    be compared against anything."""
    with pytest.raises(ValueError, match="two classes"):
        semantic.heard([OWNER], [OWNER], ["ownership"], floor=0.5, margin=0.0)


# --- the example sentences ------------------------------------------------------


def test_the_examples_carry_every_item_and_the_background() -> None:
    general = template.get_template("general")
    examples = semantic.examples_for(general)

    assert set(examples.labels) == {item.key for item in general.items} | {semantic.BACKGROUND_KEY}
    assert examples.labels.count(semantic.BACKGROUND_KEY) == len(semantic.BACKGROUND)
    assert len(examples.labels) == len(examples.texts)


def test_an_item_without_examples_has_no_class() -> None:
    """It falls back to its keywords alone, which is what it had before."""
    bare = template.Template(
        key="t",
        name="t",
        version="t.1",
        items=(
            template.TemplateItem(
                key="k",
                category="c",
                item="i",
                weight=0.5,
                keywords=("성능",),
                question="q?",
                question_about="{topic}의 q?",
            ),
        ),
    )

    assert set(semantic.examples_for(bare).labels) == {semantic.BACKGROUND_KEY}


def test_every_shipped_item_has_examples() -> None:
    for one in template.available():
        for item in one.items:
            assert item.examples, f"{one.key}:{item.key} has no example sentences"


def test_no_example_is_a_line_of_the_eval_set() -> None:
    """The examples and the background are what the embedder is graded with;
    the eval set is what it is graded on. A line copied from one to the other
    makes the harness grade its own answer key, and the number stops meaning
    anything."""
    said = {line.text.strip() for case in load_cases(DEFAULT_DATASET) for line in case.lines}
    written = {
        example for one in template.available() for item in one.items for example in item.examples
    }

    assert not (written | set(semantic.BACKGROUND)) & said


def test_the_fake_embedder_returns_unit_vectors() -> None:
    """``semantic.heard`` reads a dot product as a cosine, which holds only for
    unit vectors."""
    for vector in FakeEmbedder().embed(["제가 맡겠습니다", "네 좋습니다"]):
        assert sum(value * value for value in vector) == pytest.approx(1.0)
