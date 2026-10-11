"""Search over E's metric glossary: BM25, dense, and their RRF fusion (spec section 5).

In memory: the corpus is a few dozen fixed passages, so there is no table and
no pgvector. The question never leaves the process. Nothing here is a LangChain
retriever; the agent reaches it only through ``tools.explain_metric``.

``rank_texts`` and ``matching_line`` do the same for a team's reports, read at
question time by ``tools.search_reports``: nothing of a report is indexed or
kept here either.
"""

from __future__ import annotations

import math
import re
from collections import Counter
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
Chosen over 3 on dev; the held-out set was looked at too, so it no longer judges this
choice (docs/modules/intelligence.md)."""


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


_HANGUL_RUN = re.compile(r"[가-힣]{2,}")
_GRAMMAR = ("V", "E", "XSV", "XSA")
"""Verbs, adjectives, endings and the suffixes that make them: a question's
grammar (찾아줘, 있어), never its topic."""


def unread_pairs(question: str) -> list[str]:
    """The syllable pairs of each word in ``question`` that Kiwi does not read
    as a word of two syllables or more -- a loanword it lacks, such as 온보딩,
    which it cuts into 온 / 보 / 딩 and keeps nothing of, or 목업 (목 / 업). A
    report is matched on those pairs (온보, 보딩) as well. Kiwi can cut the same
    word one way in a question and another in a report, so its pieces alone do
    not match. A word whose pieces are grammar or a pronoun gets no pairs.
    """
    pairs: list[str] = []
    for run in _HANGUL_RUN.findall(question):
        if any(len(word) >= 2 for word in tokens(run)):
            continue
        tags = [t.tag for t in _kiwi().tokenize(run)]
        if any(tag.startswith(_GRAMMAR) for tag in tags):
            continue
        if all(tag.startswith(_LEFT_OUT) for tag in tags):
            continue
        pairs += [run[i : i + 2] for i in range(len(run) - 1)]
    return list(dict.fromkeys(pairs))


BM25_K1 = 1.5
BM25_B = 0.75
"""``rank_bm25``'s defaults, which the glossary's ``BM25Okapi`` runs with."""


def rank_texts(question: str, texts: Sequence[str]) -> list[tuple[int, float]]:
    """The ``texts`` sharing a content word with ``question``, best first, as
    (index, score): BM25 over ``tokens`` and ``unread_pairs``, ties to the
    earlier text.

    Not ``BM25Okapi``: its IDF falls to zero or below for a word found in half
    the texts or more, so over a team's one or two reports it finds nothing.
    This uses Lucene's IDF, ``ln(1 + (N - n + 0.5) / (n + 0.5))``, which stays
    above zero. A text sharing no word with the question is left out.
    """
    pairs = unread_pairs(question)
    # A pair is counted as it is written in the text. Its key starts with "\0"
    # so it never collides with a morpheme of the same spelling.
    query = list(dict.fromkeys(tokens(question))) + [f"\0{pair}" for pair in pairs]
    if not query or not texts:
        return []
    docs = [Counter(tokens(text)) for text in texts]
    lengths = [sum(doc.values()) for doc in docs]
    for doc, text in zip(docs, texts, strict=True):
        for pair in pairs:
            if found := text.count(pair):
                doc[f"\0{pair}"] = found
    average = sum(lengths) / len(docs) or 1.0
    idf = {
        word: math.log(1 + (len(docs) - n + 0.5) / (n + 0.5))
        for word in query
        if (n := sum(1 for doc in docs if word in doc))
    }
    ranked: list[tuple[int, float]] = []
    for i, (doc, length) in enumerate(zip(docs, lengths, strict=True)):
        norm = BM25_K1 * (1 - BM25_B + BM25_B * length / average)
        score = sum(
            weight * doc[word] * (BM25_K1 + 1) / (doc[word] + norm)
            for word, weight in idf.items()
            if doc[word]
        )
        if score > 0:
            ranked.append((i, score))
    return sorted(ranked, key=lambda pair: (-pair[1], pair[0]))


def matching_line(question: str, text: str, limit: int) -> str:
    """The line of ``text`` holding the most of ``question``'s content words --
    what a search shows of a report, so the reader sees why it was found. Cut
    to ``limit`` characters around the first of those words, an ellipsis
    marking each cut side. The first line when no line holds one.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    query = set(tokens(question))
    pairs = unread_pairs(question)
    best, most = lines[0], 0
    for line in lines:
        found = len(query & set(tokens(line))) + sum(1 for pair in pairs if pair in line)
        if found > most:
            best, most = line, found
    if len(best) <= limit:
        return best
    room = limit - 2  # an ellipsis on each side, at most
    words = [*query, *pairs]
    at = min((p for p in (best.find(word) for word in words) if p >= 0), default=0)
    start = max(0, min(at - room // 4, len(best) - room))
    end = start + room
    return ("…" if start else "") + best[start:end] + ("…" if end < len(best) else "")


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
