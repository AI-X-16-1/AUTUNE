"""KorNLI dataset loading — train, dev, and test are three disjoint, fixed
Hugging Face splits, never mixed or reshuffled here.

Standard recipe, matching published klue/roberta-base + KorNLI baselines:
MultiNLI + SNLI, machine-translated to Korean, is the ~950k-pair train set;
XNLI's Korean validation/test splits — human-translated, never seen during
training — are dev and test. All three come from the public
``kakaobrain/kor_nli`` dataset on the Hugging Face Hub, not committed to this
repo: it is a public benchmark, not project data, and the train split alone
is far too large to version here.

``label`` in the raw dataset is already an int 0/1/2, and its ``ClassLabel``
names are exactly ``LABELS`` in this order (checked against the dataset's own
schema via the datasets-server API, not assumed). Keep ``LABELS`` in sync with
``autune_context.pipeline.nli`` / ``base.NliScores`` — this order becomes the
``id2label`` the fine-tuned model serves in ``train.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datasets import Dataset

LABELS = ("entailment", "neutral", "contradiction")

_REPO = "kakaobrain/kor_nli"


def load_train() -> Dataset:
    """MultiNLI + SNLI, Korean. Training data only — never scored against."""
    from datasets import concatenate_datasets, load_dataset

    multi_nli = load_dataset(_REPO, "multi_nli", split="train")
    snli = load_dataset(_REPO, "snli", split="train")
    return _prepare(concatenate_datasets([multi_nli, snli]))


def load_dev() -> Dataset:
    """XNLI Korean validation split — in-training checkpoint selection only.
    Never the number reported as the model's accuracy."""
    from datasets import load_dataset

    return _prepare(load_dataset(_REPO, "xnli", split="validation"))


def load_test() -> Dataset:
    """XNLI Korean test split — read exactly once, by
    ``training.train.evaluate_test``, after checkpoint selection on dev is
    already done. Never touched while iterating on hyperparameters."""
    from datasets import load_dataset

    return _prepare(load_dataset(_REPO, "xnli", split="test"))


def _prepare(ds: Dataset) -> Dataset:
    return ds.rename_column("label", "labels")
