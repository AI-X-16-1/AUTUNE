"""Search over E's metric glossary: BM25, dense, and their RRF fusion (spec section 5).

In memory: the corpus is a few dozen fixed passages, so there is no table and
no pgvector. The question never leaves the process. Nothing here is a LangChain
retriever; the agent reaches it only through ``tools.explain_metric``.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache
from typing import Protocol

from .config import get_settings
from .glossary import Passage, passages

RRF_K = 60

_KEEP = ("N", "V", "XR", "SL", "SN")
_LEFT_OUT = ("NP", "NNB", "VX", "VCP", "VCN")
"""Pronouns (뭐, 어디), bound nouns (거), auxiliary verbs and the copula: they
appear in nearly every question and match passages by accident."""
LIGHT_WORDS = frozenset(
    {"하", "되", "있", "없", "않", "어떻", "그렇", "이렇", "보", "주", "들", "같"}
)
"""Verbs and adjectives that carry no topic of their own (하다, 있다, 어떻다)."""
TITLE_WEIGHT = 2
"""A passage's title counts this many times: it names the topic, the body explains it.
Chosen by the retrieval evaluation, held-out set included (docs/modules/intelligence.md)."""


class Retriever(Protocol):
    def search(self, question: str, k: int = 3) -> list[Passage]: ...


@cache
def _kiwi():
    from kiwipiepy import Kiwi

    return Kiwi()


def tokens(text: str) -> list[str]:
    """Content morphemes (nouns, verbs, adjectives, roots, foreign words, numbers),
    without ``_LEFT_OUT`` tags and ``LIGHT_WORDS``. A noun with its suffix is also
    kept whole (완료 + 율 -> 완료율), so 완료율 is told apart from 완료."""
    out: list[str] = []
    prev = None
    for t in _kiwi().tokenize(text):
        if t.tag == "XSN" and prev is not None and prev.tag.startswith("NN"):
            out.append(prev.form + t.form)
        prev = t
        if (
            t.tag.startswith(_KEEP)
            and not t.tag.startswith(_LEFT_OUT)
            and t.form not in LIGHT_WORDS
        ):
            out.append(t.form)
    return out


def rrf(rankings: list[list[str]], k: int = RRF_K) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda key: -scores[key])


class BM25Retriever:
    def __init__(self, corpus: Sequence[Passage]) -> None:
        from rank_bm25 import BM25Okapi

        self._corpus = list(corpus)
        self._bm25 = BM25Okapi(
            [tokens(p.title) * TITLE_WEIGHT + tokens(p.text) or ["_"] for p in self._corpus]
        )

    def ranking(self, question: str) -> list[str]:
        query = tokens(question)
        if not query:
            return []
        scores = self._bm25.get_scores(query)
        order = sorted(range(len(self._corpus)), key=lambda i: -scores[i])
        return [self._corpus[i].key for i in order if scores[i] > 0]

    def search(self, question: str, k: int = 3) -> list[Passage]:
        by_key = {p.key: p for p in self._corpus}
        return [by_key[key] for key in self.ranking(question)[:k]]


class DenseRetriever:
    def __init__(self, corpus: Sequence[Passage], model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._corpus = list(corpus)
        self._model = SentenceTransformer(model_name)
        self._vectors = self._model.encode(
            [f"{p.title} {p.text}" for p in self._corpus], normalize_embeddings=True
        )

    def ranking(self, question: str) -> list[str]:
        if not question.strip():
            return []
        q = self._model.encode([question], normalize_embeddings=True)[0]
        scores = self._vectors @ q
        order = sorted(range(len(self._corpus)), key=lambda i: -float(scores[i]))
        return [self._corpus[i].key for i in order]

    def search(self, question: str, k: int = 3) -> list[Passage]:
        by_key = {p.key: p for p in self._corpus}
        return [by_key[key] for key in self.ranking(question)[:k]]


class HybridRetriever:
    def __init__(self, corpus: Sequence[Passage], model_name: str) -> None:
        self._corpus = list(corpus)
        self._bm25 = BM25Retriever(corpus)
        self._dense = DenseRetriever(corpus, model_name)

    def search(self, question: str, k: int = 3) -> list[Passage]:
        if not question.strip():
            return []
        by_key = {p.key: p for p in self._corpus}
        fused = rrf([self._bm25.ranking(question), self._dense.ranking(question)])
        return [by_key[key] for key in fused[:k]]


@cache
def get_retriever() -> Retriever:
    settings = get_settings()
    if settings.retriever_impl == "bm25":
        return BM25Retriever(passages())
    if settings.retriever_impl == "hybrid":
        return HybridRetriever(passages(), settings.gap_classifier_backbone)
    known = "bm25, hybrid"
    raise ValueError(
        f"unknown AUTUNE_INTELLIGENCE_RETRIEVER_IMPL={settings.retriever_impl!r}; known: {known}"
    )
