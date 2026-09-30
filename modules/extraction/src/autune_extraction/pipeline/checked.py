"""``classifier_impl=llm_checked``: the LLM proposes commitments, DeBERTa checks them.

Why: on the 20 self-authored dummy eval meetings (341 gold commitments,
2026-09-28) gemini-3.5-flash-lite with the worked-example prompt scored
commitment F1 0.905 and the fine-tuned DeBERTa 0.874. They are wrong about
different utterances. Asserting only what both call a commitment reaches 0.921
(precision 0.953), but that caps recall at DeBERTa's, and DeBERTa's commitment
F1 on real Korean speech is ~0.3. So a commitment only the LLM found is kept
and shown as a candidate for a person to confirm, rather than dropped. With the
person counted as resolving every candidate correctly, F1 was 0.951 at ~2.7
candidates a meeting, a third of them true.

How it routes, commitment only:

- both say commitment -> commitment at ``AGREED_CONFIDENCE``, asserted.
- only the LLM does -> commitment at ``UNCHECKED_CONFIDENCE``, which falls
  below any ``candidate_confidence`` in (0.5, 0.9] -- the review screen.
- only DeBERTa does -> the LLM's own answer. A DeBERTa-only commitment was
  true 14 times in 68 on those meetings; surfacing it would mostly add noise.
- every other kind -> the LLM's prediction, unchanged. Only commitment was
  measured; the other four kinds are the LLM's alone, as under ``llm``.

These confidences are routing values, not probabilities: they say whether two
models agreed. The LLM gives a label, not a score (``pipeline.llm``), so there
is no probability to report.

**Nothing new leaves our infrastructure.** The LLM half is ``LlmClassifier``,
sending exactly what ``classifier_impl=llm`` sends; DeBERTa runs in process.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from autune_contracts.enums import UtteranceKind

from .base import Classifier, Prediction, give_roster
from .classifier import MODEL_VERSION_MAX

AGREED_CONFIDENCE = 0.9
"""Both models called it a commitment."""

UNCHECKED_CONFIDENCE = 0.5
"""Only the LLM did. Its runner-up is none at the same score -- the item may
not be a commitment at all, which is what a person is asked."""

VERSION_SEPARATOR = "|checked:"


class CheckedClassifier:
    """``proposer`` labels every utterance; ``checker`` confirms its commitments."""

    def __init__(self, proposer: Classifier, checker: Classifier) -> None:
        self._proposer = proposer
        self._checker = checker

    @property
    def model_version(self) -> str:
        """Both versions, so a row says which pair produced it -- or a digest of
        the two when they overrun ``ext_classifications.model_version``."""
        joined = f"{self._proposer.model_version}{VERSION_SEPARATOR}{self._checker.model_version}"
        if len(joined) <= MODEL_VERSION_MAX:
            return joined
        return f"checked:{hashlib.sha256(joined.encode('utf-8')).hexdigest()[:16]}"

    def use_roster(self, names: Sequence[str]) -> None:
        """Only the proposer sends text out (#411); the checker runs here."""
        give_roster(self._proposer, names)

    def classify(self, texts: list[str]) -> list[Prediction]:
        if not texts:
            return []
        proposed = self._proposer.classify(texts)
        checked = self._checker.classify(texts)
        if len(proposed) != len(texts) or len(checked) != len(texts):
            # The Protocol promises one per input, in order; zipping a short list
            # would pair an utterance with another one's check.
            raise ValueError(
                f"asked for {len(texts)} predictions, got {len(proposed)} proposed "
                f"and {len(checked)} checked"
            )
        return [combine(p, c) for p, c in zip(proposed, checked, strict=True)]


def combine(proposed: Prediction, checked: Prediction) -> Prediction:
    """One utterance's answer from the proposer's and the checker's."""
    if proposed.kind is not UtteranceKind.COMMITMENT:
        return proposed
    confidence = (
        AGREED_CONFIDENCE if checked.kind is UtteranceKind.COMMITMENT else UNCHECKED_CONFIDENCE
    )
    scores = dict.fromkeys(UtteranceKind, 0.0)
    scores[UtteranceKind.COMMITMENT] = confidence
    return Prediction(
        kind=UtteranceKind.COMMITMENT,
        confidence=confidence,
        scores=scores,
        none_score=1.0 - confidence,
    )
