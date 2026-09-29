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
 * One checklist item of the domain template, beside what the meeting did with
 * it — `TemplateItemRead` in `modules/gap/src/autune_gap/schemas.py`.
 *
 * **Not a contract**, for the same reason `TopicGraph` is not: the rail is
 * module C's own screen and no other module reads a checklist.
 *
 * `coverage` is `null` when the meeting has no topic graph yet. That is not
 * "covered" and must never render as it — see `TemplateComparison.analysed`.
 */
export interface TemplateChecklistItem {
  key: string;
  category: string;
  item: string;
  coverage: Coverage | null;
  /** The gap on the left of the screen this item raised, if it raised one. */
  gap_id: string | null;
  /** Somebody called that gap a false positive. The item is still not covered. */
  dismissed: boolean;
}

/**
 * The template-comparison rail — `TemplateComparison` in the same file.
 *
 * `analysed` is the field that keeps the rail honest. A meeting with no topic
 * graph raises no gaps by design (an empty graph says extraction found nothing,
 * not that the meeting discussed nothing), so every item would otherwise read
 * as covered — a full checklist of green dots for a meeting nobody has
 * processed.
 */
export interface TemplateComparison {
  template_key: string;
  name: string;
  version: string;
  analysed: boolean;
  items: TemplateChecklistItem[];
}

/** How far the meeting got with one checklist item. */
export type Coverage = "covered" | "partial" | "missing";

export const COVERAGE_LABELS: Record<Coverage, string> = {
  covered: "충족",
  partial: "미흡",
  missing: "누락",
};

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

/**
 * What proximity alone produces, as the graph writes it.
 *
 * Every other relation is something a rule read off a marker the speaker said,
 * which is why this one is separated in the UI rather than ranked beside them:
 * "these two topics were mentioned near each other" is a much weaker claim than
 * "this topic is blocked by that one". See `docs/modules/gap.md`.
 */
export const CO_OCCURS = "co_occurs";

/**
 * Korean for each relation the graph can carry.
 *
 * Deliberately **not** typed as an exhaustive `Record` over a union: `relation`
 * arrives as a plain `string` because the server's vocabulary grows without a
 * contract change (the topic graph is module C's own response body, not a
 * contract — see `TopicGraph` above). A relation with no entry here renders its
 * raw name rather than disappearing, which is the failure a reader can report.
 */
export const RELATION_LABELS: Readonly<Record<string, string>> = {
  depends_on: "의존",
  blocked_by: "막힘",
  part_of: "부분",
  alternative_to: "대안",
  [CO_OCCURS]: "함께 언급",
};

/** One relation, ready to render: ends named, direction resolved. */
export interface RelationLine {
  key: string;
  sourceLabel: string;
  targetLabel: string;
  relation: string;
  weight: number;
  /** Both directions were in the payload, so the pair reads as one line. */
  mutual: boolean;
}

/**
 * The graph's edges as lines to read, most strongly weighted first.
 *
 * Two things happen here, and both are the renderer's call rather than the
 * server's — `TopicEdgeRead` says so outright.
 *
 * **Ends are named.** An edge carries topic ids; a reader needs the labels the
 * nodes carry. An edge whose end is not among the nodes is dropped: the server
 * already refuses to write one (`graph.relation_edges`), so a line reading
 * "topic_7 → 캐시" would mean the payload disagreed with itself, and a half-named
 * relation is worse than a missing one.
 *
 * **A pair the payload states both ways collapses to one line.** A symmetric
 * relation is stored as two rows and both come back. Which relations are
 * symmetric is the server's business and it may add more, so this reads the
 * data rather than a hard-coded list of names: if A→B and B→A are both present
 * for the same relation, they are one fact said twice. A relation that is
 * genuinely one-way keeps its direction, and an asymmetric relation that
 * happens to hold both ways — 실시간 depends_on 캐시 *and* the reverse — still
 * reads correctly as a mutual line, because that is what the graph says.
 *
 * That `mutual` cannot be a false positive rests on a server constraint rather
 * than on anything here: `gap_topic_edges` carries
 * `UNIQUE(source_topic_id, target_topic_id, relation)` and
 * `CHECK(source_topic_id <> target_topic_id)`, so the only way one key can be
 * reached twice is from the two opposite directions. Loosen either constraint
 * and this collapse starts merging rows that are not a pair. Raised in review
 * of #264.
 */
export function relationLines(graph: TopicGraph | null): RelationLine[] {
  if (!graph) return [];

  const labels = new Map(graph.nodes.map((node) => [node.id, node.label]));
  const seen = new Map<string, RelationLine>();

  for (const edge of graph.edges) {
    const sourceLabel = labels.get(edge.source_topic_id);
    const targetLabel = labels.get(edge.target_topic_id);
    if (sourceLabel === undefined || targetLabel === undefined) continue;

    const [low, high] = [edge.source_topic_id, edge.target_topic_id].sort();
    const key = `${edge.relation}|${low}|${high}`;
    const existing = seen.get(key);
    if (existing) {
      // The same pair from the other side. Keep the stronger weight so the
      // ordering below does not depend on which direction arrived first.
      existing.mutual = true;
      existing.weight = Math.max(existing.weight, edge.weight);
      continue;
    }
    seen.set(key, {
      key,
      sourceLabel,
      targetLabel,
      relation: edge.relation,
      weight: edge.weight,
      mutual: false,
    });
  }

  return [...seen.values()].sort(
    (a, b) => b.weight - a.weight || a.sourceLabel.localeCompare(b.sourceLabel),
  );
}
