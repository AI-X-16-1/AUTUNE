"""The corpus, and the masker scored against it.

This file is the one place the module's own numbers are asserted rather than
described. `docs/modules/audio.md` sets masking recall at 0.95 and then 0.99;
until the corpus existed nothing could produce either figure, and a review could
only ask whether the code looked right.
"""

from __future__ import annotations

import pytest

from autune_audio.eval import corpus
from autune_audio.eval.metrics import masking_precision, masking_recall
from autune_audio.masking import mask
from autune_audio.recognition import FakeRecogniser, SpokenNumberRecogniser

RECALL_TARGET = 0.95


@pytest.fixture(scope="module")
def rows() -> tuple[corpus.Row, ...]:
    return corpus.load()


# --- the file itself ------------------------------------------------------


def test_the_corpus_has_both_directions(rows: tuple[corpus.Row, ...]) -> None:
    """A corpus of personal data can only measure half of the masker.

    Recall cannot fall when the masker covers more, so a corpus without negative
    rows scores every widening as free — which is how the account pattern came
    to eat every ISO date and nothing noticed (#125 review).
    """
    assert len(corpus.positives()) >= 15
    assert len(corpus.negatives()) >= 10


def test_every_row_declares_where_it_came_from(rows: tuple[corpus.Row, ...]) -> None:
    """`planted` and `written` are lines somebody chose; `transcript` are lines
    the recording produced. Only the third kind is evidence about real speech,
    and knowing which is which is what keeps the first two from being mistaken
    for it."""
    for row in rows:
        assert row.source in {"planted", "transcript", "written"}


def test_a_positive_row_masks_something_and_a_negative_masks_nothing(
    rows: tuple[corpus.Row, ...],
) -> None:
    for row in rows:
        if row.is_positive:
            assert "*" in row.masked, row
            assert row.categories, f"a positive row has to say what it holds: {row}"
        else:
            assert row.masked == row.text
            assert not row.categories


def test_the_two_sides_of_a_row_line_up(rows: tuple[corpus.Row, ...]) -> None:
    """`masking_recall` compares positions, so a row whose two sides have
    different token counts cannot be scored at all. Catching that here names the
    row; catching it in the harness raises from inside a loop."""
    for row in rows:
        assert len(row.text.split()) == len(row.masked.split()), row


def test_the_row_repr_does_not_carry_the_pair(rows: tuple[corpus.Row, ...]) -> None:
    """One side shows the shape and the other shows what was under it.

    The corpus holds invented values, but this object is one `print` away from
    holding a real one, and `Masked.__repr__` in `masking.py` was written under
    the same rule.
    """
    row = corpus.positives()[0]
    assert row.text not in repr(row)
    assert row.masked not in repr(row)


# --- precision, the number this module did not have -----------------------


def test_precision_falls_when_we_mask_what_we_should_not() -> None:
    reference = "버전 20260910 이사 갑니다"
    ours = "버전 ****0910 ** 갑니다"
    assert masking_precision(reference, ours).precision == 0.0
    assert masking_precision(reference, reference).precision == 1.0


def test_precision_is_one_when_we_masked_nothing() -> None:
    """A run that hid nothing has not hidden anything wrongly. That it hid
    nothing is what recall is for."""
    result = masking_precision("연락처는 010-****-5678입니다", "연락처는 010-1234-5678입니다")
    assert result.precision == 1.0
    assert result.spans_we_masked == 0


def test_recall_and_precision_answer_different_questions() -> None:
    """Masking everything scores perfectly on one and worst on the other.

    Which is the point: recall alone cannot see the cost of covering more, and
    every widening in this module has been paid for in precision.
    """
    reference = "연락처는 010-****-5678입니다"
    everything = "********* *************"
    assert masking_recall(reference, everything).recall == 1.0
    assert masking_precision(reference, everything).precision < 0.6


# --- the masker against the corpus ----------------------------------------


def _score(recogniser: object) -> tuple[float, float, list[corpus.Row]]:
    caught = expected = correct = produced = 0
    wrong: list[corpus.Row] = []
    for row in corpus.load():
        ours = mask(row.text, recogniser=recogniser).text  # type: ignore[arg-type]
        if row.is_positive:
            recall = masking_recall(row.masked, ours)
            caught += recall.spans_we_caught
            expected += recall.spans_in_reference
        precision = masking_precision(row.masked, ours)
        correct += precision.spans_that_should_be
        produced += precision.spans_we_masked
        if ours != row.masked:
            wrong.append(row)
    return (
        caught / expected if expected else 1.0,
        correct / produced if produced else 1.0,
        wrong,
    )


def test_every_row_comes_out_exactly_as_the_corpus_says() -> None:
    """The assertion the scores cannot make.

    Token recall counts a token as hidden when it holds any `*`, so
    `010-****-56789` scores as masked and the digit that leaked is invisible —
    which is the shape of #158, the bug this corpus was built after. Leaving one
    more digit in every numeric span moves neither recall nor precision off
    1.000; it moves this.

    The corpus already carries what the output should be. Comparing against it
    is the strongest check available here, and the scores are for saying how far
    off a row is once one differs.
    """
    _, _, wrong = _score(SpokenNumberRecogniser())
    assert wrong == [], [(row.text, row.masked) for row in wrong]


def test_the_masker_clears_the_recall_target() -> None:
    recall, _, _ = _score(SpokenNumberRecogniser())
    assert recall >= RECALL_TARGET, f"recall {recall:.3f} against a {RECALL_TARGET} target"


def test_the_masker_masks_nothing_it_should_not() -> None:
    """Precision does not gate a release — a leaked national ID and an
    over-masked date are not the same kind of wrong — but a drop here is a cost
    somebody decided to pay, and it should be a decision rather than a surprise.
    """
    _, precision, wrong = _score(SpokenNumberRecogniser())
    assert precision == 1.0, f"precision {precision:.3f}; rows: {wrong}"


def test_the_recogniser_is_what_clears_the_target() -> None:
    """Patterns alone do not, and the corpus says by how much.

    This is the measurement `recognition.py` exists on: three rows read out one
    digit at a time, which no pattern can describe.
    """
    with_it, _, _ = _score(SpokenNumberRecogniser())
    without, _, missed = _score(FakeRecogniser())
    assert without < RECALL_TARGET <= with_it
    assert len(missed) == 3
