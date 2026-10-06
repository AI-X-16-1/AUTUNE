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
 * One checklist item of the domain template, beside what the meeting did with
 * it — `TemplateItemRead` in `modules/gap/src/autune_gap/schemas.py`.
 *
 * **Not a contract**: the rail is module C's own screen and no other module
 * reads a checklist.
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

/**
 * One template a meeting can be held to — `TemplateRead` in
 * `modules/gap/src/autune_gap/schemas.py`, as `GET /api/gap/templates` lists it.
 *
 * `items` is a count, not the checklist: choosing a template is choosing a
 * name, and the checklist only means something next to a meeting, which is what
 * `TemplateComparison` is.
 */
export interface TemplateOption {
  key: string;
  name: string;
  version: string;
  items: number;
}

/** What `PUT /api/gap/templates/{meeting_id}` takes and returns. */
export interface TemplateSelection {
  template_key: string;
}

/**
 * What both `POST` and `DELETE /api/gap/gaps/{gap_id}/dismiss` return: the state
 * the server settled on, so the screen never assumes its own request won.
 *
 * No timestamp and no dismisser. Which teammate pressed "해당 없음" is not
 * stored anywhere (ADR 0003), and the screen has no use for when.
 */
export interface GapDismissal {
  gap_id: string;
  meeting_id: string;
  dismissed: boolean;
}

/**
 * Whether a gap was sent on to the next meeting — what `POST` and `DELETE
 * /api/gap/gaps/{id}/carry` return (`GapCarry`, #824). The flag the server
 * settled on, no timestamp and nobody's id, like `GapDismissal`.
 */
export interface GapCarry {
  gap_id: string;
  meeting_id: string;
  carried: boolean;
}

/**
 * One utterance a verdict rests on — `EvidenceRead` in
 * `modules/gap/src/autune_gap/schemas.py`. Masked text, no speaker.
 */
export interface GapEvidence {
  utterance_id: string;
  start_sec: number;
  text: string;
}

/** One term of the risk score's weighted mean — `ScorePartRead`. */
export interface ScorePart {
  /** `template` (the item's weight), `coverage` (1 − centrality) or `participation`. */
  key: string;
  weight: number;
  value: number;
}

/**
 * How a gap's score was reached — `ScoreBreakdownRead`. `score` is the stored
 * `risk_score`; a breakdown that no longer adds up to it is not sent at all.
 */
export interface ScoreBreakdown {
  parts: ScorePart[];
  /** `partial_damping`, on a partial finding only. */
  damping: number | null;
  score: number;
}

/** What a verdict rests on: a thin topic, a keyword said, meaning, or nothing. */
export type GapBasis = "topic" | "keyword" | "meaning" | "none";

/**
 * Why one gap was raised — `GapExplanationRead`. Not a contract: E scores a
 * meeting on `GapReport` and never explains a verdict.
 */
export interface GapExplanation {
  gap_id: string;
  coverage: Coverage | null;
  basis: GapBasis;
  topic_label: string | null;
  topic_centrality: number | null;
  /** The template item's keywords: what the meeting was searched for. */
  keywords: string[];
  matched_keywords: string[];
  evidence: GapEvidence[];
  breakdown: ScoreBreakdown | null;
  /** Somebody sent this gap on to the next meeting — "다음 회의 어젠다로" (#824). */
  carried: boolean;
}

/** `GET /api/gap/explanations/{meeting_id}` — `GapExplanations`. */
export interface GapExplanations {
  meeting_id: string;
  meeting_title: string;
  /** `started_at`, or when the meeting row was made if it has no start time. */
  meeting_date: string;
  partial_centrality: number;
  high_threshold: number;
  medium_threshold: number;
  gaps: GapExplanation[];
  /** One per item the rail reads as covered, in template order. */
  covered: CoveredExplanation[];
}

/**
 * Why one checklist item was read as covered — `CoveredExplanationRead`. The
 * topic is found again with the pipeline's rule; null when today's template or
 * threshold no longer reaches it, and the screen then says it cannot show why.
 */
export interface CoveredExplanation {
  item_key: string;
  topic_label: string | null;
  topic_centrality: number | null;
  evidence: GapEvidence[];
}

/** How far the meeting got with one checklist item. */
/**
 * One open gap on the team-wide list, as `GET /api/gap/gaps?team_id=` returns
 * it — `TeamGapRead` in `modules/gap/src/autune_gap/schemas.py` (#550).
 *
 * Module C's own response, mirrored by hand; `test_web_mirror.py` pins the
 * field set. **No topics and no participation**: a list across every meeting is
 * where silence read along a person would be easiest to total, and the why is
 * on the meeting's own report.
 */
export interface TeamGap {
  gap_id: string;
  meeting_id: string;
  meeting_title: string;
  /** When the meeting started, or when it was registered if it never did. */
  meeting_date: string;
  category: string;
  title: string;
  severity: GapSeverity;
  risk_score: number;
  template_item?: string | null;
  suggested_question?: string | null;
}

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
