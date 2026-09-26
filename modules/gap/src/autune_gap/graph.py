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

from autune_gap.pipeline import Entity, Relation
from autune_integrations.privacy import MASK_CHAR

# ``MASK_CHAR`` is what module A's masking leaves behind: ``010-****-5678``,
# ``k***@example.com`` (privacy.md section 2). Imported rather than written out
# here — module A masks with the patterns in ``autune_integrations.privacy``,
# and a second copy of the character in this module is a guard that stops
# matching the day the notation changes, and says nothing when it does. #250.

LABEL_MAX = 400
"""``gap_topics.label`` is ``String(400)``."""

CO_OCCURS = "co_occurs"
"""What is left when no rule could say more.

Two topics named in the same utterance are related — the speaker put them in one
sentence — and that is the whole claim. It is the weakest relation a graph can
have, it needs no model, and it is what a pair falls back to when step 2 found
no marker joining them (``build_edges``). Written both ways, because the
co-occurrence itself is symmetric: the utterance says the two go together, not
which acts on which.

Before #32 it was the only relation there was. It is still the most common one,
and a pair carrying it is a pair the meeting named together and never
explained."""

RELATION_WEIGHT = 1.0
"""What every extracted relation weighs.

Deliberately not a count. A speaker who says "정렬은 인덱스가 필요합니다" twice
has not made it twice as true, and ``gap_topic_edges`` is unique on
``(source, target, relation)`` so the repetition would not reach a second row
anyway. Co-occurrence is counted because frequency is the only evidence it has;
a relation was asserted, and an assertion is not a frequency.

If risk scoring (#35) wants to know how often a relation was restated, that is a
column on the row, not a number folded into the weight where nothing can read it
apart again."""


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


def build_edges(topics: Iterable[Topic], relations: Iterable[Relation]) -> list[Edge]:
    """Every edge of the meeting: what step 2 extracted, then co-occurrence.

    A pair the rules typed does **not** also get a ``co_occurs`` row. The typed
    relation says everything co-occurrence would and more — "A는 B가 필요해서"
    already tells us the two were named together — and keeping both would put
    two rows in the report where the meeting made one statement.

    A pair the rules said nothing about keeps its co-occurrence edge. That is
    most pairs, and dropping them would leave the graph of a meeting nobody
    spoke carefully in with no edges at all, which is a graph PageRank cannot
    read.
    """
    topics = list(topics)
    typed = relation_edges(topics, relations)
    covered = {frozenset((edge.source, edge.target)) for edge in typed}
    return typed + [
        edge
        for edge in co_occurrence_edges(topics)
        if frozenset((edge.source, edge.target)) not in covered
    ]


def relation_edges(topics: Iterable[Topic], relations: Iterable[Relation]) -> list[Edge]:
    """The relations step 2 extracted, as edges between topics.

    A relation names its two ends by the text the speaker said; a topic is every
    mention that shares a ``topic_key``. So both ends are looked up by key, and
    a relation is dropped when either end is not a topic of this meeting — an
    entity from an utterance analysis left out, or a mention the masking rule
    refused. A relation whose ends land on the same topic goes too:
    ``gap_topic_edges`` forbids the self-loop, and "검색 기능은 검색 기능이
    필요해서" is a speaker restarting a sentence, not a dependency.

    The same relation asserted in five utterances is one edge. The table is
    unique on ``(source, target, relation)``, and which utterances said it is
    evidence the two topics already carry.

    A symmetric relation (``pipeline.SYMMETRIC_RELATIONS``) arrives from the
    extractor as two triples, so nothing is mirrored here — the rules produce
    both directions where both hold, and this stays a mapping rather than a
    second place where relation semantics live.
    """
    keys = {topic.key for topic in topics}
    seen: set[tuple[str, str, str]] = set()
    edges: list[Edge] = []
    for relation in relations:
        source, target = topic_key(relation.source), topic_key(relation.target)
        triple = (source, target, relation.relation)
        if source not in keys or target not in keys or source == target or triple in seen:
            continue
        seen.add(triple)
        edges.append(
            Edge(source=source, target=target, relation=relation.relation, weight=RELATION_WEIGHT)
        )
    return edges


def co_occurrence_edges(topics: Iterable[Topic]) -> list[Edge]:
    """An edge between every two topics named in the same utterance.

    Weighted by how many utterances named both, scaled so the strongest pair is
    1. Written in both directions: ``gap_topic_edges`` is directed because the
    triples step 2 produces are, and the loader should never have to guess which
    rows are symmetric.

    Callers want ``build_edges``, which layers this under the extracted
    relations. It stays a function of its own because it is the one edge rule
    that needs nothing but the topics — the graph of a meeting the rules found
    no marker in is exactly this.
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
    that, a meeting whose topics never share an utterance would rank a topic
    named once level with one named forty times.
    It is then divided by the largest score, so "carried the meeting" reads the
    same in a meeting of eight topics and one of eighty.

    Betweenness is unweighted. NetworkX reads an edge weight there as a
    distance, and ours is a strength; inverting it would claim a precision the
    co-occurrence count does not have.

    **Parallel edges collapse to the strongest.** One pair can carry two
    relations — "A는 B의 일부인데 B가 없으면 못 한다" is ``part_of`` and
    ``depends_on`` about the same pair — and a ``DiGraph`` holds one edge per
    direction, so adding both let whichever came last in the list decide the
    weight. That made a topic's rank depend on the order rows were built in.
    Taking the maximum makes two relations at least as strong a connection as
    one, which is the only direction that reads.
    """
    if not topics:
        return {}

    graph = nx.DiGraph()
    graph.add_nodes_from(topic.key for topic in topics)
    strongest: dict[tuple[str, str], float] = {}
    for edge in edges:
        pair = (edge.source, edge.target)
        strongest[pair] = max(strongest.get(pair, 0.0), edge.weight)
    graph.add_weighted_edges_from(
        (source, target, weight) for (source, target), weight in strongest.items()
    )

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
