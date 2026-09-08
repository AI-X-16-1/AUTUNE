# 0005. No graph database — graphs are PostgreSQL rows

**Status:** Accepted
**Date:** 2026-09-08
**Deciders:** the whole team

## Context

The original plan named Neo4j for two things: module C's topic graph, and
module D's decision lineage.

Two later decisions changed what those actually are.

**ADR 0002 and issue #23 made C's graph per meeting.** C builds a fresh subgraph
for each meeting and never accumulates, because cross-meeting linking is module
D's job and two topic-matching mechanisms would disagree. A single meeting's
graph is tens of nodes and a hundred or so edges.

**A decision lineage turned out to be a chain, not a graph.** It is an ordered
sequence of versions of one decision. The relationship-graph visualisation on
screen S22 is Phase 2, and it is a rendering concern — the frontend draws it
from whatever rows it is given.

A graph database earns its place by traversing large connected graphs. Neither
module does that any more.

## Decision

There is no graph database. Topic graphs are stored as rows — `gap_topics` and
`gap_topic_edges` — and loaded into **NetworkX** in memory to compute PageRank
and betweenness. Decision lineage is `ctx_decision_versions` with a
`previous_version_id`, walked with a recursive CTE when a whole chain is needed.

## Alternatives considered

**Keep Neo4j.** Cypher is expressive and the Graph Data Science library
implements the algorithms. Rejected: at meeting scale the algorithms are not the
hard part. Measured with NetworkX on this hardware —

| Graph | PageRank | Betweenness |
| --- | --- | --- |
| 80 nodes, 200 edges | 0.5 ms | 3.7 ms |
| 300 nodes, 900 edges | 2.3 ms | 53 ms |

— and a 45-minute meeting sits at the low end of that. The cost of keeping it is
real: a service every developer runs (Neo4j wants a gigabyte or two), a deletion
hook per module because the PostgreSQL cascade does not reach it, no transaction
spanning the two stores, and separate backup and test-cleanup paths.

**Postgres recursive CTEs for everything, no NetworkX.** Rejected: PageRank and
betweenness in SQL is possible and unpleasant, and NetworkX is a pure-Python
dependency in one module rather than a service for everyone.

**Keep Neo4j only for D.** Rejected: a whole service for a linked list.

## Consequences

**Easier.** Two infrastructure services instead of four — PostgreSQL and Redis.
Meeting deletion cascades reach everything a module owns, so C and D need no
deletion hooks at all and the retention rules hold without extra code. Graph
writes share a transaction with the rows they describe. Integration tests need
no separate namespace or cleanup.

**Harder.** Graph work happens in application code: C loads its rows into
NetworkX each run rather than querying a graph engine. Multi-hop traversal over
a large accumulated graph would need rethinking — but nothing in the MVP does
that, and if it appears it is a new decision, not a regression.

**A note on the portfolio.** The original plan listed "Knowledge Graph, graph
algorithms" as module C's showcase, with Neo4j named. That work is unchanged:
entities and relations are still extracted, a graph is still constructed, and
centrality still decides which topics carried a meeting. What changes is that
the algorithms are run rather than called on a managed service.

**Revisit if** a use case needs traversal across an accumulated multi-meeting
graph, which today is deliberately module D's job and is done with retrieval
rather than graph queries.
