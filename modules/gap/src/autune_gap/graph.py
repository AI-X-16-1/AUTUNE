"""One meeting's topic graph, as pure functions.

No database, no model, no network. ``service`` feeds this the entities the
extractor found and writes what comes back; everything that decides what the
graph *is* lives here, so it can be argued with — and tested — without starting
Postgres or loading spaCy.

Steps 3 to 5 of docs/modules/gap.md: topics, the edges between them, PageRank
and betweenness, and the participation matrix.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import combinations

import networkx as nx

from autune_gap.pipeline import Entity

MASK_CHAR = "*"
"""What module A's masking leaves behind: ``010-****-5678``, ``k***@example.com``
(privacy.md section 2)."""

LABEL_MAX = 400
"""``gap_topics.label`` is ``String(400)``."""

CO_OCCURS = "co_occurs"
"""The only relation there is until relation extraction lands (#32).

Two topics named in the same utterance are related — the speaker put them in
one sentence. It is the weakest relation a graph can have and the only one that
needs no model; #32 replaces it with directed triples, and PageRank starts
reading direction then. Until that happens every edge is written both ways."""


@dataclass(frozen=True)
class Topic:
    """One node: a thing the meeting named, and everywhere it was named.

    ``key`` is what makes two mentions the same topic. ``label`` is how the
    meeting first said it — what a person reading the report recognises.
    """

    key: str
    label: str
    utterance_ids: tuple[str, ...]
    """Meeting order, each utterance once."""


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    relation: str
    weight: float
    """In ``(0, 1]``, the strongest edge of the meeting at 1 — the range
    ``gap_topic_edges`` accepts."""


@dataclass(frozen=True)
class Centrality:
    pagerank: float
    """Normalised so the topic that carried the meeting scores 1."""
    betweenness: float


def topic_key(text: str) -> str:
    """Whitespace collapsed, case folded. "검색  기능" and "검색 기능" are one topic.

    Deliberately no more than that. Merging "검색" into "검색 기능" is a
    judgement about meaning, and a wrong merge hides a topic inside another one
    — the gap report would then say the meeting covered something it did not.
    """
    return " ".join(text.split()).casefold()


def build_topics(entities: Iterable[Entity], utterance_order: Iterable[str]) -> list[Topic]:
    """Group entities into topics, in order of first mention.

    ``utterance_order`` is the utterances under analysis, in meeting order. An
    entity from any other utterance is dropped: an utterance absent from it was
    left out on purpose — a participant who did not consent — and a topic built
    from their speech would put their words back into the report.

    An entity carrying the mask character is dropped too. What is left of a
    masked phone number is not a topic, and ``0101234****`` as a graph node
    would carry the unmasked half of one into a report the whole team reads.
    """
    position = {utterance_id: index for index, utterance_id in enumerate(utterance_order)}
    kept = sorted(
        (e for e in entities if e.utterance_id in position and MASK_CHAR not in e.text),
        key=lambda e: position[e.utterance_id],
    )

    labels: dict[str, str] = {}
    mentions: dict[str, list[str]] = {}
    for entity in kept:
        key = topic_key(entity.text)
        if not key:
            continue
        labels.setdefault(key, " ".join(entity.text.split())[:LABEL_MAX])
        seen = mentions.setdefault(key, [])
        if entity.utterance_id not in seen:
            seen.append(entity.utterance_id)

    return [Topic(key=key, label=labels[key], utterance_ids=tuple(mentions[key])) for key in labels]


def co_occurrence_edges(topics: Iterable[Topic]) -> list[Edge]:
    """An edge between every two topics named in the same utterance.

    Weighted by how many utterances named both, scaled so the strongest pair is
    1. Written in both directions: ``gap_topic_edges`` is directed because the
    triples #32 produces will be, and the loader should never have to guess
    which rows are symmetric.
    """
    by_utterance: dict[str, set[str]] = {}
    for topic in topics:
        for utterance_id in topic.utterance_ids:
            by_utterance.setdefault(utterance_id, set()).add(topic.key)

    pairs: Counter[tuple[str, str]] = Counter()
    for keys in by_utterance.values():
        pairs.update(combinations(sorted(keys), 2))
    if not pairs:
        return []

    strongest = max(pairs.values())
    edges: list[Edge] = []
    for (a, b), count in sorted(pairs.items()):
        weight = count / strongest
        edges.append(Edge(source=a, target=b, relation=CO_OCCURS, weight=weight))
        edges.append(Edge(source=b, target=a, relation=CO_OCCURS, weight=weight))
    return edges


def centrality(topics: list[Topic], edges: Iterable[Edge]) -> dict[str, Centrality]:
    """PageRank and betweenness for each topic, both in ``[0, 1]``.

    PageRank is personalised by how many utterances named each topic. Without
    that, a meeting whose topics never share an utterance — every early meeting,
    until #32 — would rank a topic named once level with one named forty times.
    It is then divided by the largest score, so "carried the meeting" reads the
    same in a meeting of eight topics and one of eighty.

    Betweenness is unweighted. NetworkX reads an edge weight there as a
    distance, and ours is a strength; inverting it would claim a precision the
    co-occurrence count does not have.
    """
    if not topics:
        return {}

    graph = nx.DiGraph()
    graph.add_nodes_from(topic.key for topic in topics)
    graph.add_weighted_edges_from((edge.source, edge.target, edge.weight) for edge in edges)

    total = sum(len(topic.utterance_ids) for topic in topics)
    personalization = {topic.key: len(topic.utterance_ids) / total for topic in topics}
    pagerank = nx.pagerank(graph, weight="weight", personalization=personalization)
    top = max(pagerank.values())
    betweenness = nx.betweenness_centrality(graph, normalized=True)

    return {
        key: Centrality(pagerank=_unit(pagerank[key] / top), betweenness=_unit(betweenness[key]))
        for key in graph.nodes
    }


def participation(
    topic: Topic, speaker_of: Mapping[str, str], participants: Iterable[str]
) -> dict[str, bool]:
    """Participant id -> whether they spoke on this topic. Nothing else.

    Not how often, not for how long: a count here is a speaking ratio by
    another name, and this reaches the whole team (privacy.md section 3). Every
    participant under analysis gets an entry, because silence is the evidence a
    gap is raised on — a participant with no entry is one nobody considered.
    """
    spoke = {speaker_of[u] for u in topic.utterance_ids if u in speaker_of}
    return {participant: participant in spoke for participant in participants}


def _unit(value: float) -> float:
    """Clamp float noise — PageRank can land a hair past 1 — into the range the
    check constraints enforce."""
    return min(1.0, max(0.0, value))
