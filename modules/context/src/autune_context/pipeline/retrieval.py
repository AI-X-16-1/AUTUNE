"""Hybrid retrieval: KURE-v1 dense (pgvector) + BM25 (kiwipiepy), fused with RRF.

Retrieve broad, re-rank narrow — this stage casts a wide net (``retrieve_top_k``,
default 50); the cross-encoder in ``service`` trims it to ``rerank_top_k``.
PostgreSQL has no Korean full-text analyzer, so BM25 runs in application code and
the two rankings are combined with reciprocal rank fusion rather than in one
query. See docs/modules/context.md, "AI stack".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from autune_context.models import CtxEmbedding
from autune_core import Meeting

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from autune_context.pipeline.topics import TopicSegment


@dataclass(frozen=True)
class Candidate:
    linked_meeting_id: str
    linked_meeting_date: date | None
    topic_label: str
    similarity: float
    """Best dense cosine similarity seen for this meeting (0..1)."""
    fusion_score: float
    """Reciprocal-rank-fusion score across the dense and lexical rankings."""


def reciprocal_rank_fusion[K](rankings: Sequence[Sequence[K]], *, k: int) -> dict[K, float]:
    """Standard RRF: score(d) = sum over rankings of 1 / (k + rank(d)), rank 1-based."""
    scores: dict[K, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return scores


class HybridRetriever:
    def __init__(self, session: Session, *, retrieve_top_k: int, rrf_k: int) -> None:
        self._session = session
        self._top_k = retrieve_top_k
        self._rrf_k = rrf_k

    def retrieve(
        self,
        topic: TopicSegment,
        *,
        team_id: str,
        before: datetime,
        exclude_meeting_id: str,
    ) -> list[Candidate]:
        """Past-meeting candidates for one topic, best fusion score first."""
        corpus = self._corpus(team_id, before, exclude_meeting_id)
        if not corpus:
            return []

        dense = self._dense_ranking(topic.vector, team_id, before, exclude_meeting_id)
        lexical = _bm25_ranking(topic.text, corpus)

        fused = reciprocal_rank_fusion([[m for m, _ in dense], lexical], k=self._rrf_k)
        best_sim = dict(dense)

        candidates = [
            Candidate(
                linked_meeting_id=meeting_id,
                linked_meeting_date=corpus[meeting_id].date,
                topic_label=corpus[meeting_id].label,
                similarity=best_sim.get(meeting_id, 0.0),
                fusion_score=score,
            )
            for meeting_id, score in fused.items()
        ]
        candidates.sort(key=lambda c: c.fusion_score, reverse=True)
        return candidates[: self._top_k]

    def _visible(self, team_id: str, before: datetime):
        """A meeting D is allowed to link to: this team, in the past, still inside
        its retention window.

        Shared between the dense and corpus queries so they cannot drift apart —
        `retrieve()` indexes `corpus` by every id either ranking returns, so a
        meeting visible to one and not the other is a `KeyError` at best and a
        retention-window bypass at worst (see docs/architecture/privacy.md,
        section 4).
        """
        now = datetime.now(tz=UTC)
        return (
            CtxEmbedding.kind == "topic",
            Meeting.team_id == team_id,
            Meeting.started_at < before,
            or_(Meeting.expires_at.is_(None), Meeting.expires_at > now),
        )

    def _dense_ranking(
        self, vector: list[float], team_id: str, before: datetime, exclude: str
    ) -> list[tuple[str, float]]:
        distance = CtxEmbedding.embedding.cosine_distance(vector)
        stmt = (
            select(CtxEmbedding.meeting_id, distance.label("distance"))
            .join(Meeting, Meeting.id == CtxEmbedding.meeting_id)
            .where(*self._visible(team_id, before))
            .where(CtxEmbedding.meeting_id != exclude)
            .order_by(distance)
            .limit(self._top_k)
        )
        # Keep the best (smallest distance) row per meeting.
        best: dict[str, float] = {}
        for meeting_id, dist in self._session.execute(stmt):
            similarity = 1.0 - float(dist)
            if meeting_id not in best or similarity > best[meeting_id]:
                best[meeting_id] = similarity
        return sorted(best.items(), key=lambda kv: kv[1], reverse=True)

    def _corpus(self, team_id: str, before: datetime, exclude: str) -> dict[str, _CorpusEntry]:
        stmt = (
            select(CtxEmbedding.meeting_id, CtxEmbedding.ref_label, Meeting.started_at)
            .join(Meeting, Meeting.id == CtxEmbedding.meeting_id)
            .where(*self._visible(team_id, before))
            .where(CtxEmbedding.meeting_id != exclude)
        )
        corpus: dict[str, _CorpusEntry] = {}
        for meeting_id, label, started_at in self._session.execute(stmt):
            entry = corpus.get(meeting_id)
            started_date = started_at.date() if started_at else None
            if entry is None:
                corpus[meeting_id] = _CorpusEntry(label=label, date=started_date, text=label)
            else:
                corpus[meeting_id] = _CorpusEntry(
                    label=entry.label, date=entry.date, text=f"{entry.text} {label}"
                )
        return corpus


@dataclass(frozen=True)
class _CorpusEntry:
    label: str
    date: date | None
    text: str


def _bm25_ranking(query: str, corpus: dict[str, _CorpusEntry]) -> list[str]:
    from rank_bm25 import BM25Okapi

    meeting_ids = list(corpus)
    tokenized = [_tokens(corpus[m].text) for m in meeting_ids]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(_tokens(query))
    ranked = sorted(zip(meeting_ids, scores, strict=True), key=lambda kv: kv[1], reverse=True)
    return [meeting_id for meeting_id, score in ranked if score > 0.0]


_KIWI_CACHE: list = []


def _tokens(text: str) -> list[str]:
    from kiwipiepy import Kiwi

    if not _KIWI_CACHE:
        _KIWI_CACHE.append(Kiwi())
    kiwi = _KIWI_CACHE[0]
    return [t.form for t in kiwi.tokenize(text) if t.tag[0] in ("N", "V", "S")]
