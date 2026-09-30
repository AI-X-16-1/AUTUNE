"""Which utterances the embedder is unsure about, and what an LLM is asked about them.

``semantic.heard`` decides every utterance on the embedding alone, and on the
eval set it gets some of them wrong in both directions: "인덱스 재색인이 먼저
끝나야 정렬 로직을 붙일 수 있습니다" settles a dependency and scores 0.51, under
the floor; "인기순 정렬 대신 실시간 개인화로 가는 거죠" settles nothing on the
checklist and scores 0.56 for cold start, over it. Both sit close to the line.

This module splits the utterances three ways by their ``semantic.Ranking``:

- **confident** — an item won clearly (score and lead both high). The
  embedding's answer is used as it is and nothing is asked.
- **ambiguous** — some item is close enough to be possible and nothing won
  clearly. These, and only these, go to the verifier, each with the few item
  candidates the embedder ranked highest.
- **ignored** — nothing on the checklist is near, or the background class won
  clearly. Nothing is asked and nothing is heard.

**The verifier checks candidates; it decides nothing else.** It is shown one
utterance and at most a handful of the template's own items, and answers yes
or no for each. It cannot name an item it was not shown — ``resolve`` keeps
only keys that were offered — and what it confirms enters ``detect.compare``
through the same ``heard`` argument the embedder uses, where a heard item is
partial at most. Coverage and risk are ``detect``'s, unchanged.

Pure: rankings and texts in, questions and item keys out. The verifier is
called by ``service``; see ``pipeline.base.TemplateVerifier`` for what that
sends where.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from autune_gap.semantic import BACKGROUND_KEY, Ranking

if TYPE_CHECKING:
    # Types only: ``template`` imports ``pipeline.base`` (#456), so a runtime import
    # here would close a cycle through ``pipeline.registry``.
    from autune_gap.template import Template


class Triage(StrEnum):
    CONFIDENT = "confident"
    AMBIGUOUS = "ambiguous"
    IGNORED = "ignored"


@dataclass(frozen=True)
class TriageThresholds:
    """Everything numeric, from ``config``. Nothing here is hardcoded."""

    confident_score: float
    """An item that wins with at least this score, and ``confident_lead`` ahead
    of the runner-up, is taken without asking."""

    confident_lead: float
    """Also how far the background class must lead for an utterance to be
    dismissed without asking."""

    candidate_score: float
    """An item scoring below this is not offered to the verifier at all. An
    utterance with no item at or above it is ignored."""

    candidates: int
    """How many items, at most, one question offers — the embedder's nearest."""


@dataclass(frozen=True)
class Decision:
    """What triage made of one utterance.

    ``item`` is set for a confident one; ``candidates`` for an ambiguous one,
    nearest first."""

    index: int
    triage: Triage
    item: str | None = None
    candidates: tuple[str, ...] = ()


def triage(rankings: Sequence[Ranking], thresholds: TriageThresholds) -> list[Decision]:
    """One decision per utterance, in order."""
    decisions = []
    for index, ranking in enumerate(rankings):
        clear = ranking.lead >= thresholds.confident_lead
        if (
            ranking.winner != BACKGROUND_KEY
            and clear
            and ranking.score >= thresholds.confident_score
        ):
            decisions.append(Decision(index, Triage.CONFIDENT, item=ranking.winner))
            continue
        if ranking.winner == BACKGROUND_KEY and clear:
            decisions.append(Decision(index, Triage.IGNORED))
            continue

        candidates = tuple(
            name
            for name, score in ranking.classes
            if name != BACKGROUND_KEY and score >= thresholds.candidate_score
        )[: thresholds.candidates]
        if candidates:
            decisions.append(Decision(index, Triage.AMBIGUOUS, candidates=candidates))
        else:
            decisions.append(Decision(index, Triage.IGNORED))
    return decisions


@dataclass(frozen=True)
class Candidate:
    """One template item as the verifier sees it: its name, what settling it
    means, and a few example sentences. No key — the prompt letters the
    candidates, and the answer is mapped back by letter."""

    key: str
    item: str
    question: str
    examples: tuple[str, ...]


@dataclass(frozen=True)
class Question:
    """One ambiguous utterance and the candidates it is checked against.

    The utterance text and nothing about it: no speaker, no time, no meeting,
    no neighbouring line. Whether a sentence discussed an item is a question
    about the sentence."""

    utterance: str
    candidates: tuple[Candidate, ...]


def questions(
    decisions: Sequence[Decision],
    speech: Sequence[str],
    template: Template,
    *,
    examples_per_candidate: int,
    limit: int,
) -> list[tuple[int, Question]]:
    """The ambiguous decisions as questions, at most ``limit`` of them.

    ``limit`` bounds how much of one meeting can leave in a run. Questions go
    in the order the utterances were said; those past the limit are not asked,
    and ``service`` gives them the embedding's own answer.
    """
    if limit < 0:
        raise ValueError(f"limit must be 0 or more, not {limit}")
    items = {item.key: item for item in template.items}
    ambiguous = [decision for decision in decisions if decision.triage is Triage.AMBIGUOUS]
    out = []
    for decision in ambiguous[:limit]:
        candidates = tuple(
            Candidate(
                key=key,
                item=items[key].item,
                question=items[key].question,
                examples=items[key].examples[:examples_per_candidate],
            )
            for key in decision.candidates
        )
        out.append(
            (decision.index, Question(utterance=speech[decision.index], candidates=candidates))
        )
    return out


def resolve(
    decisions: Sequence[Decision],
    asked: Sequence[tuple[int, Question]],
    answers: Sequence[frozenset[str]],
) -> frozenset[str]:
    """The item keys the meeting said: every confident item, and every
    candidate the verifier confirmed.

    An answer naming a key its question did not offer is dropped — the
    verifier checks candidates and cannot add to them. An ambiguous utterance
    that was not asked (past ``limit``, or its batch failed) contributes
    nothing here; ``service`` decides what stands in for it.
    """
    if len(asked) != len(answers):
        raise ValueError(f"{len(answers)} answers for {len(asked)} questions")

    heard = {decision.item for decision in decisions if decision.item is not None}
    for (_, question), confirmed in zip(asked, answers, strict=True):
        offered = {candidate.key for candidate in question.candidates}
        heard |= confirmed & offered
    return frozenset(heard)
