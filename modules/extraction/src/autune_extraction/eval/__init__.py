"""Module B's evaluation harness — the module KPI, run on demand by its owner.

    uv run --package autune-extraction python -m autune_extraction.eval \
        --eval-set dataset/extraction_eval.jsonl \
        --predictions runs/deberta-v1.jsonl

ADR 0006 defines what this reports: the classifier's five-way macro F1 is the
metric, and action item F1 is derived from it and printed beside the best
published figure for the task.
"""

from __future__ import annotations

from .dataset import EvalExample, EvalSet, EvalSetError, load_eval_set, load_predictions
from .metrics import (
    AMI_BEST_PUBLISHED_ACTION_ITEM_F1,
    KINDS,
    ClassScore,
    Report,
    action_item_f1,
    score,
)

__all__ = [
    "AMI_BEST_PUBLISHED_ACTION_ITEM_F1",
    "KINDS",
    "ClassScore",
    "EvalExample",
    "EvalSet",
    "EvalSetError",
    "Report",
    "action_item_f1",
    "load_eval_set",
    "load_predictions",
    "score",
]
