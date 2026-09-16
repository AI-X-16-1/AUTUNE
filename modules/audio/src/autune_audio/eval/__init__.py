"""Evaluation for module A: CER, WER, DER, hallucination, terms, masking.

Targets are in docs/modules/audio.md. Scoring takes structures rather than a
model, so it runs without a GPU and means the same thing across model versions.

CER is the Korean primary metric; see ``korean`` for why WER is not. MER and
PIER are HiKE's code-switching metrics; see ``codeswitch`` for their definitions
and ``hike`` for the corpus.
"""

from .codeswitch import (
    MixedErrorRate,
    PointOfInterestErrorRate,
    mixed_error_rate,
    point_of_interest_error_rate,
)
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
    "MixedErrorRate",
    "PointOfInterestErrorRate",
    "MaskingRecall",
    "Hallucination",
    "TermAccuracy",
    "character_error_rate",
    "word_error_rate",
    "mixed_error_rate",
    "point_of_interest_error_rate",
    "diarization_error_rate",
    "masking_recall",
    "hallucinated_characters",
    "term_accuracy",
    "normalise",
]
