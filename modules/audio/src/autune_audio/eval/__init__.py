"""Evaluation for module A: CER, WER, DER, hallucination, terms, masking.

Targets are in docs/modules/audio.md. Scoring takes structures rather than a
model, so it runs without a GPU and means the same thing across model versions.

CER is the Korean primary metric; see ``korean`` for why WER is not.
"""

from .korean import (
    CharacterErrorRate,
    Hallucination,
    TermAccuracy,
    character_error_rate,
    hallucinated_characters,
    normalise,
    term_accuracy,
)
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
    "CharacterErrorRate",
    "WordErrorRate",
    "MaskingRecall",
    "Hallucination",
    "TermAccuracy",
    "character_error_rate",
    "word_error_rate",
    "diarization_error_rate",
    "masking_recall",
    "hallucinated_characters",
    "term_accuracy",
    "normalise",
]
