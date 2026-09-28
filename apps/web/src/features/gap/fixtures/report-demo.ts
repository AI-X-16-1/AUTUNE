import type { GapReport, TemplateComparison, TopicGraph } from "../types";

/**
 * One meeting's gap report, for looking at S20 without a database behind it.
 *
 * The screen and its three hooks are the real ones; only the answers are made
 * up. It exists so the layout can be reviewed against
 * `docs/design/AUTUNE Spec 03 회의 후.dc.html` before the pipeline is running
 * locally — the same reason `features/transcript` keeps `live-demo`.
 *
 * **It is not an estimate of anything.** The scores here were chosen to put one
 * finding in each band, not measured; C's precision figure comes from
 * `python -m autune_gap.eval` over real meetings and from nowhere else.
 *
 * What it does hold itself to is the shape the server actually produces:
 *
 * - every item of the `general` template appears in the rail, because the rail
 *   is the checklist and not the findings;
 * - a covered item has no gap row, which is how the server reports covered at
 *   all — `gap_gaps` stores only `partial` and `missing`;
 * - a missing item scores exactly its template weight (`detect.score`: with no
 *   topic, coverage and participation are unmeasurable and dropped), so
 *   `ownership` at weight 0.9 is 0.90 rather than a rounder-looking number;
 *   the partial findings are damped and land below it;
 * - `gap_id` on a rail item is the gap on the left, so the two sides of the
 *   screen are one finding seen twice.
 */
export const DEMO_MEETING_ID = "mtg_demo";

/** The topic that joined two conversations, which is not the one that carried
 * the meeting — `gap_topics` keeps the two numbers apart. Declared before the
 * graph that reads it: these are module-level constants, evaluated in order. */
const BETWEENNESS: Record<string, number> = {
  topic_demo_personalisation: 1.0,
  topic_demo_popular: 0.36,
  topic_demo_coldstart: 0.42,
  topic_demo_target: 0.18,
};

export const DEMO_REPORT: GapReport = {
  contract_version: "1.0",
  meeting_id: DEMO_MEETING_ID,
  gaps: [
    {
      id: "gap_demo_ownership",
      category: "ownership",
      title: "담당자와 기한 — 논의되지 않았습니다",
      severity: "high",
      risk_score: 0.9,
      template_item: "담당자와 기한",
      related_topic_ids: [],
      suggested_question: "이 일은 누가 맡고, 언제까지 끝내기로 합니까?",
    },
    {
      id: "gap_demo_risk",
      category: "risk",
      title: "리스크·예외 처리 — 충분히 다뤄지지 않았습니다",
      severity: "medium",
      risk_score: 0.54,
      template_item: "리스크·예외 처리",
      related_topic_ids: ["topic_demo_exception"],
      suggested_question: "이 방향이 실패하거나 예외 상황이 생기면 무엇을 합니까?",
    },
    {
      id: "gap_demo_dependency",
      category: "dependency",
      title: "의존성·선행 조건 — 충분히 다뤄지지 않았습니다",
      severity: "medium",
      risk_score: 0.51,
      template_item: "의존성·선행 조건",
      related_topic_ids: ["topic_demo_integration"],
      suggested_question: "이 일을 시작하기 전에 먼저 끝나 있어야 하는 것은 무엇입니까?",
    },
    {
      id: "gap_demo_next_step",
      category: "next_step",
      title: "다음 단계 — 충분히 다뤄지지 않았습니다",
      severity: "low",
      risk_score: 0.38,
      template_item: "다음 단계",
      related_topic_ids: ["topic_demo_followup"],
      suggested_question: "이 회의 다음에 실제로 일어나는 일은 무엇입니까?",
    },
  ],
  topics: [
    topic("topic_demo_personalisation", "검색 개인화", 1.0),
    topic("topic_demo_target", "성능 목표", 0.78),
    topic("topic_demo_popular", "인기순 정렬", 0.64),
    topic("topic_demo_coldstart", "콜드스타트", 0.31),
    topic("topic_demo_exception", "예외 처리", 0.22),
    topic("topic_demo_integration", "연동 일정", 0.19),
    topic("topic_demo_followup", "후속 작업", 0.15),
  ],
  // Ids with no number attached, read along a topic and never along a person
  // (docs/architecture/privacy.md section 3). Participant ids, not user ids.
  participation: [
    {
      topic_id: "topic_demo_personalisation",
      spoke: ["prt_01", "prt_02", "prt_03"],
      silent: [],
    },
    {
      topic_id: "topic_demo_target",
      spoke: ["prt_01", "prt_03"],
      silent: ["prt_02"],
    },
    {
      topic_id: "topic_demo_popular",
      spoke: ["prt_02"],
      silent: ["prt_01", "prt_03"],
    },
    {
      topic_id: "topic_demo_coldstart",
      spoke: ["prt_01"],
      silent: ["prt_02", "prt_03"],
    },
    {
      topic_id: "topic_demo_exception",
      spoke: ["prt_03"],
      silent: ["prt_01", "prt_02"],
    },
    {
      topic_id: "topic_demo_integration",
      spoke: ["prt_02"],
      silent: ["prt_01", "prt_03"],
    },
    {
      topic_id: "topic_demo_followup",
      spoke: ["prt_01"],
      silent: ["prt_02", "prt_03"],
    },
  ],
};

export const DEMO_GRAPH: TopicGraph = {
  meeting_id: DEMO_MEETING_ID,
  nodes: DEMO_REPORT.topics!.map((one) => ({
    id: one.id,
    label: one.label,
    centrality: one.centrality,
    betweenness: BETWEENNESS[one.id] ?? 0,
  })),
  // Both directions, the way `gap_topic_edges` stores a symmetric relation —
  // the renderer collapses the pair, the server does not.
  edges: [
    ...pair("topic_demo_personalisation", "topic_demo_popular", 1.0),
    ...pair("topic_demo_personalisation", "topic_demo_target", 0.82),
    ...pair("topic_demo_personalisation", "topic_demo_coldstart", 0.61),
    ...pair("topic_demo_popular", "topic_demo_coldstart", 0.44),
    ...pair("topic_demo_target", "topic_demo_exception", 0.3),
    ...pair("topic_demo_coldstart", "topic_demo_integration", 0.21),
  ],
};

export const DEMO_COMPARISON: TemplateComparison = {
  template_key: "general",
  name: "기본",
  version: "general.1",
  analysed: true,
  items: [
    // No gap row, so the server read this one back as covered.
    {
      key: "success_criteria",
      category: "measurement",
      item: "성공 기준·측정 지표",
      coverage: "covered",
      gap_id: null,
      dismissed: false,
    },
    {
      key: "ownership",
      category: "ownership",
      item: "담당자와 기한",
      coverage: "missing",
      gap_id: "gap_demo_ownership",
      dismissed: false,
    },
    {
      key: "risk",
      category: "risk",
      item: "리스크·예외 처리",
      coverage: "partial",
      gap_id: "gap_demo_risk",
      dismissed: false,
    },
    {
      key: "dependency",
      category: "dependency",
      item: "의존성·선행 조건",
      coverage: "partial",
      gap_id: "gap_demo_dependency",
      dismissed: false,
    },
    {
      key: "next_step",
      category: "next_step",
      item: "다음 단계",
      coverage: "partial",
      gap_id: "gap_demo_next_step",
      dismissed: false,
    },
  ],
};

function topic(id: string, label: string, centrality: number) {
  return { id, label, centrality, utterance_ids: [] };
}

function pair(source: string, target: string, weight: number) {
  return [
    {
      source_topic_id: source,
      target_topic_id: target,
      relation: "co_occurs",
      weight,
    },
    {
      source_topic_id: target,
      target_topic_id: source,
      relation: "co_occurs",
      weight,
    },
  ];
}
