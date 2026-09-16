/**
 * Types for this feature.
 *
 * Anything that crosses the API boundary comes from @autune/contracts, which is
 * generated from the Pydantic models. Never hand-write a mirror of a contract.
 */
export type { Gap, GapReport, GapSeverity, Topic } from "@autune/contracts";

import type { Gap, GapSeverity } from "@autune/contracts";

/**
 * One topic's participation, as `GapReport.participation` carries it.
 *
 * Re-exported under a readable name: the generator numbers a definition whose
 * name it has already used for the array alias, so the contract's entry type is
 * `Participation1`. Renaming it here is not hand-writing a mirror — it is the
 * generated type, spelled the way this feature reads it.
 *
 * **`spoke` and `silent` are ids with no number attached, and that is the
 * whole point.** Read along a topic ("이 토픽에 백엔드 발언 없음") it is what a
 * gap is. Totalled along a *person* it becomes the speaking ratio that
 * `docs/architecture/privacy.md` section 3 keeps private to its subject —
 * rebuilt out of data that is individually harmless. No component in this
 * feature may group it by participant.
 */
export type { Participation1 as TopicParticipation } from "@autune/contracts";

/**
 * The topic graph as `GET /api/gap/topics/{meeting_id}` returns it —
 * `TopicGraphRead` in `modules/gap/src/autune_gap/schemas.py`.
 *
 * **Not a contract, so it is not generated.** The contracts package covers what
 * crosses between modules; module E receives `GapReport` and never draws a
 * graph, so this shape is module C's own response body and no generator reads
 * it. `test_the_web_topic_graph_mirror_is_current` in `modules/gap/tests` pins
 * the Python side's field set and names this file, so a field added there fails
 * a test rather than going missing here.
 */
export interface TopicNode {
  id: string;
  label: string;
  /** PageRank, normalised so the topic that carried the meeting is 1. */
  centrality: number;
  /**
   * Betweenness, which the contract's `Topic` does not carry. The topic that
   * *joined* two conversations, which is a different question from the topic
   * that carried the meeting — kept apart because risk scoring weights them
   * differently.
   */
  betweenness: number;
}

/**
 * One relation, directed. A symmetric relation arrives as two entries, one per
 * direction: the server does not collapse the pair, because #32 replaces
 * `co_occurs` with triples where the collapse would lose which topic acted on
 * which. Drop the direction here if a renderer wants one line per pair.
 */
export interface TopicEdge {
  source_topic_id: string;
  target_topic_id: string;
  relation: string;
  weight: number;
}

export interface TopicGraph {
  meeting_id: string;
  nodes: TopicNode[];
  edges: TopicEdge[];
}

/**
 * The order S20 lists severities in: HIGH expanded, MEDIUM collapsed, LOW
 * separate.
 *
 * Only HIGH is shown by default — `modules/gap/CLAUDE.md` and the module doc
 * both say so, and the reason is C's metric. A false gap costs user trust and a
 * missed one costs nothing the team did not already have, so the screen leads
 * with what the pipeline is most sure of.
 */
export const SEVERITIES = ["high", "medium", "low"] as const satisfies readonly GapSeverity[];

export const SEVERITY_LABELS: Record<GapSeverity, string> = {
  high: "높음",
  medium: "중간",
  low: "낮음",
};

/** Gaps of one severity, in the order the server ranked them. */
export function bySeverity(gaps: readonly Gap[], severity: GapSeverity): Gap[] {
  return gaps.filter((gap) => gap.severity === severity);
}
