"""Which template items the meeting *said*, read by sentence meaning rather than
by keyword.

Template comparison has two evidence sources (``detect.compare``): the topic
graph, and the speech. The speech used to be read for an item's keywords only,
and a keyword is a noun. A meeting that settles ownership with "정렬 로직은
이건우님이 맡고 다음 주 금요일까지 초안을 봅니다" says no noun that names the
item, so the item came back ``missing`` and a false gap reached the screen. The
eval set calls that cause ``no-noun`` and, before this module, it was every
false positive the harness found — out of reach of a keyword list and of a
better extractor alike.

This reads the same speech by what it means. Each utterance is compared against
example sentences of what settling each item sounds like (``TemplateItem.examples``)
and against ``BACKGROUND``, sentences that settle nothing, and it counts for the
item it is nearest to — or for nothing.

**Nearest class, not a fixed cutoff per item.** Measured against KURE-v1 on the
eval set, a cutoff cannot separate the two: "네 알겠습니다. 그럼 여기서
마치겠습니다" sits at 0.65 to "그 작업은 제가 맡겠습니다", above the 0.61 of
an utterance that really did settle an owner. Short Korean sentences share
their endings, and the ending moves the cosine. Put the same
sentence up against ``BACKGROUND`` and it lands there at 0.86, which is the
point of having a class for nothing.

**What this decides is only "it was said".** ``detect.classify`` ranks the
evidence: a spoken hit with no topic behind it is ``partial`` and never
``covered``, and that holds here too. A sentence close to "제가 맡겠습니다" is
a reason not to call the item missing, not proof the meeting settled it.

Pure: vectors in, item keys out. ``service`` runs the embedder; no text is
stored or logged here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    # Types only: ``template`` imports ``pipeline.base`` (#456), so a runtime import
    # here would close a cycle through ``pipeline.registry``.
    from autune_gap.template import Template


BACKGROUND_KEY = "_background"
"""The class an utterance lands in when it settles nothing on the checklist.
Not a template item key: the leading underscore cannot come out of a template
file, whose keys are identifiers."""

BACKGROUND: tuple[str, ...] = (
    "네 좋습니다",
    "알겠습니다, 그럼 오늘은 여기까지 하죠",
    "지난주랑 달라진 건 없습니다",
    "오늘은 진행 상황만 공유드릴게요",
    "혹시 질문 있으신가요",
    "조금 늦어지고 있습니다",
)
"""Sentences a meeting says that settle no item: agreement, a status line,
closing the meeting.

Korean, because they are compared against Korean speech; they are model input,
like the template examples, not copy anybody reads. **Not taken from the eval
set** — writing them from the fixtures would make the harness grade its own
answer key. They are shared by every template, because what counts as
"settles nothing" does not depend on which checklist the meeting is held to."""


@dataclass(frozen=True)
class Examples:
    """Every example sentence the comparison reads, flattened, with the class
    each one belongs to. Built per meeting from the template; see ``service``."""

    labels: tuple[str, ...]
    texts: tuple[str, ...]


def examples_for(template: Template) -> Examples:
    """The template's example sentences followed by ``BACKGROUND``.

    An item with no ``examples`` has no class and cannot be heard — it falls
    back to its keywords alone, which is what it had before this module.
    """
    labels: list[str] = []
    texts: list[str] = []
    for item in template.items:
        for example in item.examples:
            labels.append(item.key)
            texts.append(example)
    for sentence in BACKGROUND:
        labels.append(BACKGROUND_KEY)
        texts.append(sentence)
    return Examples(labels=tuple(labels), texts=tuple(texts))


@dataclass(frozen=True)
class Ranking:
    """One utterance's classes, nearest first: ``(class, score)`` pairs, where a
    class's score is the utterance's best cosine with any of its examples.

    Every class appears, ``BACKGROUND_KEY`` included, so the runner-up is always
    there to measure a lead against. ``heard`` and ``verification.triage`` both
    read this; neither recomputes the similarity."""

    classes: tuple[tuple[str, float], ...]

    @property
    def winner(self) -> str:
        return self.classes[0][0]

    @property
    def score(self) -> float:
        return self.classes[0][1]

    @property
    def lead(self) -> float:
        """How far the winner is ahead of the runner-up."""
        return self.classes[0][1] - self.classes[1][1]


def rank(
    utterances: Sequence[Sequence[float]],
    examples: Sequence[Sequence[float]],
    labels: Sequence[str],
) -> list[Ranking]:
    """Every utterance's classes, nearest first, in utterance order.

    ``utterances`` and ``examples`` are unit vectors (``SentenceEmbedder.embed``
    returns them so), and ``labels`` names the class of each example.
    """
    if len(examples) != len(labels):
        raise ValueError(f"{len(examples)} example vectors for {len(labels)} labels")
    if not len(utterances) or not len(examples):
        return []

    classes = sorted(set(labels))
    if len(classes) < 2:
        # A lone class always wins, so "nearest" would mean "above the floor"
        # and nothing would be compared against anything.
        raise ValueError("need at least two classes, one of them BACKGROUND")

    similarity = np.asarray(utterances, dtype=float) @ np.asarray(examples, dtype=float).T
    label_array = np.asarray(labels)
    per_class = np.stack(
        [similarity[:, label_array == name].max(axis=1) for name in classes], axis=1
    )

    rankings = []
    for row in per_class:
        order = np.argsort(row)[::-1]
        rankings.append(
            Ranking(classes=tuple((classes[index], float(row[index])) for index in order))
        )
    return rankings


def heard(
    utterances: Sequence[Sequence[float]],
    examples: Sequence[Sequence[float]],
    labels: Sequence[str],
    *,
    floor: float,
    margin: float,
) -> frozenset[str]:
    """Item keys at least one utterance was nearest to.

    An utterance counts for the winning class of its ``rank`` when that class is
    an item, the score reaches ``floor``, and it leads the runner-up by at least
    ``margin``. This is the embedding-only decision; ``verification`` layers an
    LLM check over the utterances it leaves uncertain.

    One class per utterance. "재색인 일정은 인프라팀에 확인하고 다음 회의에서
    공유하죠" arguably settles a dependency and a next step at once, and it will
    count for one of them; letting it count for every class above the floor
    would bring back the shared-ending problem this module exists to avoid.
    """
    found = (
        nearest_item(ranking, floor=floor, margin=margin)
        for ranking in rank(utterances, examples, labels)
    )
    return frozenset(key for key in found if key is not None)


def nearest_item(ranking: Ranking, *, floor: float, margin: float) -> str | None:
    """The item one utterance counts for on the embedding alone, or ``None``."""
    if ranking.winner != BACKGROUND_KEY and ranking.score >= floor and ranking.lead >= margin:
        return ranking.winner
    return None
