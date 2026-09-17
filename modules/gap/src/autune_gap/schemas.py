"""Internal schemas for module C.

Anything another module needs belongs in ``packages/contracts``, not here.

The topic graph below is read by this module's own screen (S20, #48) and by
nothing else. E receives ``GapReport`` and never calls an endpoint, so a shape
that exists only for a visualization would be four modules' business for no
reason — and ``contracts`` is frozen additively, which is a price worth paying
for a payload that crosses a boundary and not for one that does not.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TopicNodeRead(BaseModel):
    """One node of the graph S20 draws.

    Carries ``betweenness``, which ``autune_contracts.Topic`` does not. The
    contract gives E what it scores a meeting on; a renderer also wants the
    topic that *joined* two conversations, and that is the second number
    ``gap_topics`` keeps beside PageRank rather than blending into it.

    No utterance ids here. The evidence behind a topic is transcript content,
    it is already in the report, and a graph that carries it makes every node
    of a long meeting ship its quotations whether or not anyone opens one.
    """

    id: str
    label: str
    centrality: float = Field(ge=0, le=1)
    betweenness: float = Field(ge=0, le=1)


class TopicEdgeRead(BaseModel):
    """One relation between two topics, exactly as ``gap_topic_edges`` holds it.

    A symmetric relation is stored as two rows, one per direction (see
    ``models.GapTopicEdge``), and both come back here. Collapsing the pair on
    the way out would be a rule that ``relation == "co_occurs"`` is undirected,
    and relation extraction (#32) is about to produce directed triples where
    the same collapse would lose which topic acted on which. A renderer that
    wants one line per pair drops the direction it does not need; this endpoint
    does not decide that for it.
    """

    source_topic_id: str
    target_topic_id: str
    relation: str
    weight: float = Field(gt=0, le=1)


class TopicGraphRead(BaseModel):
    """This meeting's topic graph, for drawing.

    Nodes come in the same order as ``GapReport.topics`` — most central first —
    so a screen showing both never has to reconcile two orderings.

    Deliberately no participation matrix. Who spoke on a topic is in the
    report, keyed by topic id, and the graph is the one surface where a node
    could quietly acquire a per-person number attached to a picture. See
    docs/architecture/privacy.md section 3.
    """

    meeting_id: str
    nodes: list[TopicNodeRead] = Field(default_factory=list)
    edges: list[TopicEdgeRead] = Field(default_factory=list)
