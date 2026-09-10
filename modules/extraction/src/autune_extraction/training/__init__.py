"""Fine-tune the five-way utterance classifier.

Run against a dataset built by ``autune_extraction.labeling``:

    uv run --package autune-extraction --extra training \
        python -m autune_extraction.training \
        --data dataset/ami --out runs/kf-deberta-v1

**This module never touches the database.** It reads JSONL files a person
pointed it at and writes a checkpoint directory. It holds no session, imports no
models, and has no path to ``utterances`` -- ADR 0003 and 0006 keep user
corrections out of training labels, and the cheapest way to keep that true is
for the training code to be unable to reach them. A test asserts it rather than
this docstring promising it.

The split that matters is between what needs a GPU and what does not.
``dataset`` is arithmetic over label strings -- reading rows, mapping labels to
ids, weighting classes -- and runs in CI on every push. ``train`` is the Trainer
wiring, needs ``transformers``, and lives behind the ``training`` extra. Nearly
everything worth getting wrong is in the first one.
"""

from .dataset import (
    LABEL_TO_ID,
    LABELS,
    TrainExample,
    class_weights,
    label_counts,
    read_split,
)

__all__ = [
    "LABELS",
    "LABEL_TO_ID",
    "TrainExample",
    "class_weights",
    "label_counts",
    "read_split",
]
