"""Turning an annotated corpus into training labels for the five-way classifier.

The label definitions come from the AMI Meeting Corpus rather than being invented
here, because AMI already annotates these boundaries. ``ami`` holds that mapping
as code so the corpus loader, the LLM labelling prompt, and the hand-correction
pass all read the same definition of each class instead of three paraphrases of
the table in ``docs/modules/extraction.md``.

AMI is CC BY 4.0 and requires attribution wherever results are published.
Corpora are downloaded per machine and never committed.
"""

from __future__ import annotations

from .ami import (
    ADJACENCY_PAIRS,
    DIALOGUE_ACTS,
    EXCLUDED_ACTS,
    PRECEDENCE,
    Evidence,
    Label,
    label_for,
)

__all__ = [
    "ADJACENCY_PAIRS",
    "DIALOGUE_ACTS",
    "EXCLUDED_ACTS",
    "PRECEDENCE",
    "Evidence",
    "Label",
    "label_for",
]
