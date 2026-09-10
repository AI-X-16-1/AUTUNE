"""Reading a labelled split, and the arithmetic over its labels.

Deliberately free of ``torch`` and ``transformers``: every judgement in this
file -- which label is which id, how a skewed corpus is weighted, what counts as
a malformed row -- is checkable without a model, and so it is checked on every
push rather than on the machine that happens to have the extra installed.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from autune_contracts.enums import UtteranceKind

SPLITS: tuple[str, ...] = ("train", "validation", "test")

LABELS: tuple[str, ...] = (
    "commitment",
    "decision",
    "open_question",
    "concern",
    "ambiguous",
)
"""Label order, written out rather than derived from ``UtteranceKind``.

The ids these produce are baked into the checkpoint: a model trained with
``decision`` at index 1 answers 1 for a decision forever. Deriving the order
from the enum would let a harmless-looking reorder of a contract enum silently
repoint every id in every checkpoint already trained, and the test that checks
it would move with it and keep passing.

``test_label_order_is_pinned`` fails on a change here, which is the point: a new
label is a new model, not a new constant.
"""

LABEL_TO_ID: dict[str, int] = {label: index for index, label in enumerate(LABELS)}
ID_TO_LABEL: dict[int, str] = {index: label for label, index in LABEL_TO_ID.items()}


class DatasetError(Exception):
    """The split is missing or malformed. Never carries utterance text.

    A message that quotes the line it choked on would put corpus text into a
    traceback, a log line and whatever collects them. Line number and reason are
    enough to find it by hand.
    """


@dataclass(frozen=True)
class TrainExample:
    """One labelled utterance, with its text.

    Unlike ``eval.EvalExample``, which drops the text on purpose, training needs
    it -- it is the input. The difference is what the text *is*: the evaluation
    set scores real meetings, and this reads an annotated public corpus (AMI,
    CC BY 4.0) that a person passed on the command line. Pointing this at
    meeting content would be a different act, and ``test_training_cannot_reach
    _the_database`` is what stops it becoming an easy one.
    """

    utterance_id: str
    text: str
    label: str

    @property
    def label_id(self) -> int:
        return LABEL_TO_ID[self.label]


def read_split(directory: Path, name: str) -> list[TrainExample]:
    """Read ``<directory>/<name>.jsonl`` as written by ``autune_extraction.labeling``.

    Raises on an empty or missing file rather than returning nothing. A training
    run that reports a loss over zero examples is worse than one that stops --
    it produces a checkpoint, and the checkpoint looks like the others.
    """
    path = directory / f"{name}.jsonl"
    if not path.exists():
        raise DatasetError(
            f"no {name} split at {path}. Build one with "
            "`python -m autune_extraction.labeling --corpus ... --out ...`"
        )

    examples: list[TrainExample] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            label = UtteranceKind(row["kind"]).value
            example = TrainExample(utterance_id=row["utterance_id"], text=row["text"], label=label)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise DatasetError(f"{path} line {number}: {type(exc).__name__}") from exc

        if not example.text.strip():
            raise DatasetError(f"{path} line {number}: empty text")
        if example.utterance_id in seen:
            raise DatasetError(f"{path} line {number}: duplicate utterance_id")
        seen.add(example.utterance_id)
        examples.append(example)

    if not examples:
        raise DatasetError(f"{path} has no examples")
    return examples


def label_counts(examples: list[TrainExample]) -> Counter[str]:
    """How many of each label. Printed before a run so a collapsed class is
    visible before an hour of training rather than after it."""
    return Counter(example.label for example in examples)


def class_weights(examples: list[TrainExample]) -> list[float]:
    """Inverse-frequency weights, in ``LABELS`` order, normalised to mean 1.

    The corpus is skewed and the rare classes are the ones the product needs.
    ``ambiguous`` recall measured 0.172 on the first run (#115): the confirmation
    DM missed 83% of the utterances it exists for, and 53 of those were recorded
    as settled decisions instead of asked about. An unweighted loss is what
    produces that -- guessing the common class is most of the accuracy.

    A class with no examples gets weight 0 rather than dividing by zero. It also
    cannot be learned, so ``label_counts`` is printed beside this and a zero
    there is a reason to stop, not to proceed with a weight.
    """
    counts = label_counts(examples)
    total = len(examples)
    raw = [0.0 if counts[label] == 0 else total / (len(LABELS) * counts[label]) for label in LABELS]
    present = [weight for weight in raw if weight > 0]
    if not present:
        raise DatasetError("no examples carry a known label")
    mean = sum(present) / len(present)
    return [weight / mean for weight in raw]
