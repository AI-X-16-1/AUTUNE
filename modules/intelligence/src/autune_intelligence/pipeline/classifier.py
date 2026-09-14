"""Gap pattern classification: SetFit few-shot, and a fake.

Two implementations. ``setfit`` is imported inside the class that needs it —
importing at module scope would make ``apps/api`` load a sentence-transformers
stack to serve a health check, and would make this module's unit tests need
one, the same reasoning module C's ``SpacyNer`` gives for its own lazy import.
"""

from __future__ import annotations

import re
from typing import Any

from autune_core import get_logger

from .base import PATTERN_TYPES, Classification

log = get_logger(__name__)

_SEED_VERSION = "gap-patterns-seed-v1"
"""Bumped whenever ``_SEED_EXAMPLES`` changes, since that changes what the
model trains on without changing the backbone name. Part of
``SetFitGapClassifier.model_version``."""

_SEED_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("일정이 언제까지인지 정해지지 않았습니다", "schedule"),
    ("다음 마일스톤 날짜가 논의되지 않았습니다", "schedule"),
    ("마감 기한을 아직 못 정했습니다", "schedule"),
    ("출시 일정에 대한 합의가 없었습니다", "schedule"),
    ("이 작업을 누가 맡을지 정해지지 않았습니다", "ownership"),
    ("담당자가 아직 지정되지 않았습니다", "ownership"),
    ("책임 소재가 불분명합니다", "ownership"),
    ("이 기능은 누가 구현하나요", "ownership"),
    ("예산 범위가 논의되지 않았습니다", "budget"),
    ("비용을 누가 승인하는지 정해지지 않았습니다", "budget"),
    ("이 작업에 드는 비용이 얼마인지 나오지 않았습니다", "budget"),
    ("고객사 의견을 확인하지 않았습니다", "stakeholder"),
    ("이해관계자와 공유되지 않았습니다", "stakeholder"),
    ("관련 팀의 승인을 받지 않았습니다", "stakeholder"),
    ("장애가 났을 때 대응 계획이 없습니다", "risk"),
    ("이 변경의 리스크를 아무도 짚지 않았습니다", "risk"),
    ("실패했을 때의 롤백 방안이 없습니다", "risk"),
    ("요구사항 범위가 명확하지 않습니다", "scope"),
    ("이 기능의 명세가 정의되지 않았습니다", "scope"),
    ("어디까지가 이번 작업 범위인지 불분명합니다", "scope"),
    ("성능 목표 수치가 정의되지 않았습니다", "scope"),
)
"""A seed set, not an evaluated corpus. Nobody has labeled real gap titles
against this vocabulary yet — there is no eval set the way #10 and #13 name
one for modules B and C. Revisit the moment real ``GapReport`` traffic exists
to check against, the same seam #13 documents for C's NER recall. Written in
Korean because meeting titles are — see ``docs/product/glossary.md``."""

_OTHER_CONFIDENCE_THRESHOLD = 0.55
"""Below this the top class's probability is treated as "not confident
enough", and the gap is bucketed into ``other`` instead of forced into one of
the six trained labels. ``other`` in ``PATTERN_TYPES`` exists for exactly this
— see ``base.PATTERN_TYPES``."""


class SetFitGapClassifier:
    """A SetFit few-shot classifier, trained once per process from the seed set.

    Trained rather than loaded: unlike module B's DeBERTa classifier or module
    D's KURE embedder, there is no published checkpoint to point at — SetFit's
    whole shape is fitting a small head on top of a general sentence-embedding
    backbone from a handful of labeled examples, so "the checkpoint" is the
    backbone name plus ``_SEED_EXAMPLES``, both in this file. Fitting the
    handful of contrastive pairs SetFit builds from twenty-one examples takes
    seconds on CPU, not the minutes a from-scratch fine-tune would.
    """

    def __init__(self, backbone: str) -> None:
        self._backbone = backbone
        self._model: Any = None

    @property
    def model_version(self) -> str:
        """``<backbone>+gap-patterns-seed-v1`` — the backbone **and** the seed set.

        The backbone alone does not say which examples the head was fit from;
        recording both is what lets a pattern distribution be compared across
        a seed-set change the way module C's ``model_version`` lets a topic
        graph be compared across a spaCy version.
        """
        return f"{self._backbone}+{_SEED_VERSION}"

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from datasets import Dataset  # noqa: PLC0415
            from setfit import SetFitModel, Trainer, TrainingArguments  # noqa: PLC0415
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "the SetFit gap classifier needs the 'local-models' extra: "
                "uv sync --package autune-intelligence --extra local-models"
            ) from exc

        model = SetFitModel.from_pretrained(self._backbone)
        texts = [text for text, _ in _SEED_EXAMPLES]
        labels = [label for _, label in _SEED_EXAMPLES]
        trainer = Trainer(
            model=model,
            args=TrainingArguments(batch_size=16, num_epochs=1),
            train_dataset=Dataset.from_dict({"text": texts, "label": labels}),
        )
        trainer.train()
        self._model = model
        log.info("intelligence_gap_classifier_trained", backbone=self._backbone)

    def classify(self, texts: list[str]) -> list[Classification]:
        if not texts:
            return []
        self._load()

        probs = self._model.predict_proba(texts)
        classes = list(self._model.model_head.classes_)
        results: list[Classification] = []
        for row in probs:
            best_index = max(range(len(classes)), key=lambda i: row[i])
            confidence = float(row[best_index])
            label = classes[best_index] if confidence >= _OTHER_CONFIDENCE_THRESHOLD else "other"
            results.append(Classification(pattern_type=label, confidence=confidence))
        return results


_KEYWORD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "schedule",
        re.compile(r"일정|마감|기한|날짜|스케줄|milestone|deadline|schedule", re.IGNORECASE),
    ),
    ("ownership", re.compile(r"담당|책임|오너|맡을|owner|assignee|ownership", re.IGNORECASE)),
    ("budget", re.compile(r"예산|비용|승인.{0,4}비용|budget|cost", re.IGNORECASE)),
    (
        "stakeholder",
        re.compile(r"이해관계자|고객사?\s*의견|관련\s*팀|stakeholder|customer", re.IGNORECASE),
    ),
    ("risk", re.compile(r"리스크|장애|롤백|위험|risk|rollback|incident", re.IGNORECASE)),
    ("scope", re.compile(r"범위|명세|요구사항|스펙|scope|spec|requirement", re.IGNORECASE)),
)
"""Checked in order; first match wins. ``PATTERN_TYPES`` order, minus
``other`` — the fallback when nothing matches."""


class FakeGapClassifier:
    """Deterministic, no weights, no network. What the tests run.

    Keys first on ``PATTERN_TYPES`` appearing verbatim in the input — module
    E's own local-dev mock payloads (``scripts/mock_payloads.py``) and this
    module's integration tests already pass one of the six names as
    ``Gap.category``, and this makes that keep meaning what it says rather
    than being reclassified into something else by a fake model. Falls back to
    a small keyword table for text that does not already carry the label, so
    a fake run still produces a non-trivial distribution. Not an approximation
    of the real classifier's accuracy — see module C's ``FakeNer`` for the
    same caveat about its own fake.
    """

    model_version = "fake"

    def classify(self, texts: list[str]) -> list[Classification]:
        return [self._classify_one(text) for text in texts]

    def _classify_one(self, text: str) -> Classification:
        lowered = text.lower()
        for label in PATTERN_TYPES:
            if label != "other" and label in lowered:
                return Classification(pattern_type=label, confidence=1.0)
        for label, pattern in _KEYWORD_PATTERNS:
            if pattern.search(text):
                return Classification(pattern_type=label, confidence=0.75)
        return Classification(pattern_type="other", confidence=0.0)
