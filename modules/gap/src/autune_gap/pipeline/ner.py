"""Entity extraction: a Korean spaCy model, and a fake.

Two implementations and deliberately no third. There is no external-API option
because extraction runs over every utterance of a meeting, and handing a whole
transcript to somebody else's model is a decision about where personal data goes
rather than a value of ``AUTUNE_GAP_NER_IMPL`` — see ``base``.

``spacy`` is imported inside the class that needs it. Importing at module scope
would make ``apps/api`` load a model stack to serve a health check, and would
make this module's unit tests need one.
"""

from __future__ import annotations

import re
from typing import Any

from autune_core import get_logger

from .base import Entity

log = get_logger(__name__)

_SPACY_LABELS: dict[str, str] = {
    # spaCy's Korean pipelines emit the news-text inventory. Only these four
    # carry anything a topic graph can use; ORG, LOC, NORP and the rest are
    # dropped rather than forced into a bucket they do not belong in.
    "PS": "person",
    "PERSON": "person",
    "DT": "date",
    "DATE": "date",
    "QT": "metric",
    "QUANTITY": "metric",
    "PERCENT": "metric",
}
"""spaCy label -> one of ``ENTITY_LABELS``. Unmapped labels are dropped.

``feature`` and ``system`` have no spaCy equivalent — they are this product's
vocabulary, not a general NER inventory — so a general model cannot supply them
and the fine-tuning issue (#13) is where they come from. Until then the graph is
built from the three a general model does give, and that limit is visible here
rather than hidden behind an empty class.
"""


class SpacyNer:
    """A Korean spaCy pipeline, in this process.

    Loaded once per process by the registry, not per task: the pipeline is tens
    of megabytes and a per-call load would dominate a step that is otherwise
    milliseconds.

    Meeting speech is spoken Korean and the available models are trained on
    written Korean, so recall here is expected to be poor — issue #13 carries
    the fine-tuning that fixes it. The seam exists so everything downstream can
    be built and measured before that lands.
    """

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._nlp: Any = None

    @property
    def model_version(self) -> str:
        return self._model_name

    def _load(self) -> None:
        if self._nlp is not None:
            return
        try:
            import spacy  # noqa: PLC0415
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
            # Named rather than left as a bare ImportError because this is the
            # default implementation: the first person to run a worker hits it,
            # and "No module named 'spacy'" does not say that an extra exists or
            # what it is called.
            raise RuntimeError(
                "the spaCy extractor needs the 'local-models' extra: "
                "uv sync --package autune-gap --extra local-models"
            ) from exc

        try:
            self._nlp = spacy.load(self._model_name)
        except OSError as exc:
            raise RuntimeError(
                f"spaCy model {self._model_name!r} is not installed: "
                f"python -m spacy download {self._model_name}"
            ) from exc
        log.info("gap_ner_loaded", model=self._model_name)

    def extract(self, utterances: list[tuple[str, str]]) -> list[Entity]:
        if not utterances:
            return []
        self._load()

        found: list[Entity] = []
        texts = [text for _, text in utterances]
        for (utterance_id, _), doc in zip(utterances, self._nlp.pipe(texts), strict=True):
            for span in doc.ents:
                label = _SPACY_LABELS.get(span.label_)
                if label is None:
                    continue
                found.append(Entity(text=span.text, label=label, utterance_id=utterance_id))
        return found


_FAKE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\d+\s*(?:%|퍼센트|건|초|ms|밀리초)", "metric"),
    (r"\d{1,2}\s*월\s*\d{1,2}\s*일|다음\s*주|이번\s*주|내일|모레", "date"),
    (r"(?:검색|정렬|추천|로그인|결제|알림|필터)\s*(?:기능|화면)?", "feature"),
    (r"(?:서버|DB|캐시|API|배치|큐|인덱스)", "system"),
)
"""Checked in order, first match per span wins. Ordered most specific first:
"3월 1일" is a date and not a metric, though both begin with a digit."""


class FakeNer:
    """Deterministic, no weights, no network. What the tests run.

    Keys on the vocabulary a product meeting actually uses, because that is what
    the graph is built from. It is **not** an approximation of the model's
    accuracy and must not be used to estimate it; it exists so the topic graph,
    the centrality pass and the participation matrix can be built and tested
    before #13 lands.

    Deliberately conservative. A miss costs a node the graph never had; a false
    hit puts a node in the graph that centrality then ranks, and a gap raised
    off a topic nobody discussed is exactly the false positive C's precision
    target exists to avoid.
    """

    model_version = "fake"

    def extract(self, utterances: list[tuple[str, str]]) -> list[Entity]:
        found: list[Entity] = []
        for utterance_id, text in utterances:
            found.extend(
                Entity(text=span, label=label, utterance_id=utterance_id)
                for span, label in _claim_spans(text)
            )
        return found


def _claim_spans(text: str) -> list[tuple[str, str]]:
    """Every span one pattern claims, in the order they appear in ``text``.

    A character belongs to at most one entity. Without that, "3월 1일" is a date
    to one pattern and the leading "3" is nothing to another, but a text like
    "검색 API" would hand the same characters to two labels the day a pattern is
    widened — and the graph would grow a node nobody can point at in the
    transcript.
    """
    claimed: list[tuple[int, int, str, str]] = []
    for pattern, label in _FAKE_PATTERNS:
        for match in re.finditer(pattern, text):
            start, end = match.span()
            if any(
                start < other_end and other_start < end for other_start, other_end, _, _ in claimed
            ):
                continue
            claimed.append((start, end, match.group(0).strip(), label))
    claimed.sort()
    return [(span, label) for _, _, span, label in claimed if span]
