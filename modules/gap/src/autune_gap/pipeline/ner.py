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
from .spoken import Token, is_plausible, noun_terms

log = get_logger(__name__)

_SPACY_LABELS: dict[str, str] = {
    "PS": "person",
    "DT": "date",
    "TI": "date",
    "QT": "metric",
}
"""spaCy label -> one of ``ENTITY_LABELS``.

``TI`` (time) and ``DT`` (date) are one class here. "다음 주 화요일" and "오후
3시" are both when-something-happens, and nothing downstream weights them
differently.

``feature`` and ``system`` have no entry, because a general model has no label
for them — they are this product's vocabulary, not a general NER inventory. #13
is where they come from. Until then the graph is built from the three a general
model does give, and that limit is visible here rather than hidden.
"""

_IGNORED_LABELS: dict[str, str] = {
    "LC": (
        "Place. A meeting room or a market is not a topic the graph weights, and "
        "promoting one would put a node in the graph nobody discussed."
    ),
    "OG": (
        "Organisation. In a product meeting these are the team itself and the "
        "vendors it names, neither of which is a topic the meeting covered."
    ),
}
"""Labels the model emits and this module deliberately drops, and why.

Together with ``_SPACY_LABELS`` this accounts for all six labels
``ko_core_news_lg`` 3.8.0 declares (``DT LC OG PS QT TI``, the KLUE inventory).
Accounting for every one is the point: a label nobody decided about is
indistinguishable from a label somebody forgot, and the forgetting is silent —
an unmapped label yields no entity, not an error. ``TI`` was missing from the
first version of this map, so "오후 3시" was dropped and nothing said so.

The same argument module B wrote down for ``EXCLUDED_ACTS``.
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
        self._version = ""
        self._unaccounted: set[str] = set()

    @property
    def model_version(self) -> str:
        """``ko_core_news_lg-3.8.0`` — the name **and** the version.

        The name alone is not a version. ``ko_core_news_lg`` is a pipeline that
        ships a new release with every spaCy minor, so a graph built with 3.7
        and one built with 3.8 would carry the same string and gap precision
        could not be compared across the two — which is the whole reason this is
        recorded on every row.

        Loading is what makes the version knowable: it is in the pipeline's own
        ``meta``, not in the configuration. That makes this property load the
        model, which is unusual for a property and is the honest shape — the
        registry loads once per process anyway, and there is no version to
        report for a model that will not load.
        """
        self._load()
        return f"{self._model_name}-{self._version}"

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
                f"spaCy model {self._model_name!r} is not installed. It ships in "
                "the 'local-models' extra: uv sync --package autune-gap --extra "
                "local-models"
            ) from exc
        self._version = str(self._nlp.meta["version"])
        log.info("gap_ner_loaded", model=self._model_name, version=self._version)

    def extract(self, utterances: list[tuple[str, str]]) -> list[Entity]:
        """The model's entities, filtered, plus the noun terms it has no label for.

        Two passes over one parse. The NER pass gives people, dates and
        quantities; ``spoken.noun_terms`` gives the compounds a written-Korean
        model has no label for, which on a product meeting is everything the
        meeting was about. Neither pass is enough alone — see ``spoken`` for
        what each one finds on the shared fixtures.

        Within an utterance the result is in the order the speaker said things,
        so the label a topic keeps is the first way the meeting phrased it
        rather than whichever pass happened to run first.
        """
        if not utterances:
            return []
        self._load()

        found: list[Entity] = []
        texts = [text for _, text in utterances]
        for (utterance_id, _), doc in zip(utterances, self._nlp.pipe(texts), strict=True):
            spans: list[tuple[int, str, str]] = []
            claimed: list[tuple[int, int]] = []
            for span in doc.ents:
                label = _SPACY_LABELS.get(span.label_)
                if label is None:
                    self._note_unaccounted(span.label_)
                    continue
                # Claimed whether or not it is plausible: a span the model read
                # as a one-letter person is still spoken for, and letting a
                # noun run swallow it would put the rejection back in the graph
                # under another name.
                claimed.append((span.start_char, span.end_char))
                if is_plausible(label, span.text):
                    spans.append((span.start_char, span.text, label))

            tokens = [
                Token(text=token.text, tag=token.tag_, start=token.idx, end=token.idx + len(token))
                for token in doc
            ]
            spans.extend((start, term, "term") for start, term in noun_terms(tokens, claimed))

            found.extend(
                Entity(text=text, label=label, utterance_id=utterance_id)
                for _, text, label in sorted(spans, key=lambda entry: entry[0])
            )
        return found

    def _note_unaccounted(self, label: str) -> None:
        """Say something the first time a label neither mapped nor ignored appears.

        Dropping it silently is what the two maps exist to prevent, and a model
        swapped through ``AUTUNE_GAP_NER_MODEL`` can emit an inventory nobody
        here decided about. Logged rather than raised: a label we did not expect
        should not end a meeting's analysis, and the log line is what tells
        somebody to decide about it.

        No entity text is logged — the label is the model's vocabulary, the span
        would be the meeting's.
        """
        if label in _IGNORED_LABELS or label in self._unaccounted:
            return
        self._unaccounted.add(label)
        log.warning(
            "gap_ner_unaccounted_label",
            label=label,
            model=self._model_name,
            version=self._version,
        )


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
