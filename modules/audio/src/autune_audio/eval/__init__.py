"""Evaluation for module A: WER, DER and PII masking recall.

Targets are in docs/modules/audio.md. Scoring takes structures rather than a
model, so it runs without a GPU and means the same thing across model versions.
"""

from .metrics import (
    MaskingRecall,
    Turn,
    WordErrorRate,
    diarization_error_rate,
    masking_recall,
    word_error_rate,
)

__all__ = [
    "Turn",
    "WordErrorRate",
    "MaskingRecall",
    "word_error_rate",
    "diarization_error_rate",
    "masking_recall",
]
