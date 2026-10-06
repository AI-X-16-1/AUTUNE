import type {
  GapExplanations,
  GapReport,
  TemplateComparison,
  TemplateOption,
} from "../types";

/**
 * One meeting's gap report, for looking at S20 without a database behind it.
 *
 * The screen and its hooks are the real ones; only the answers are made
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
      risk_score: 0.43,
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

/** What the picker offers on the demo route: the two templates the package ships. */
export const DEMO_TEMPLATES: TemplateOption[] = [
  { key: "feature_planning", name: "기능 기획", version: "general.4+feature_planning.2", items: 10 },
  { key: "general", name: "기본", version: "general.4", items: 5 },
];

/**
 * Why each demo gap was raised. Every breakdown is the arithmetic
 * `detect.score_breakdown` does with the shipped weights (0.4 · 0.4 · 0.2,
 * partial damping 0.7) and adds up to the gap's `risk_score` above, so the demo
 * cannot show a score its own explanation contradicts.
 */
export const DEMO_EXPLANATIONS: GapExplanations = {
  meeting_id: DEMO_MEETING_ID,
  meeting_title: "검색 개인화 기획 회의",
  meeting_date: "2026-09-21T01:00:00Z",
  partial_centrality: 0.4,
  high_threshold: 0.7,
  medium_threshold: 0.5,
  gaps: [
    {
      gap_id: "gap_demo_ownership",
      carried: false,
      coverage: "missing",
      basis: "none",
      topic_label: null,
      topic_centrality: null,
      keywords: ["담당", "책임", "기한", "마감", "일정", "언제까지", "데드라인"],
      matched_keywords: [],
      evidence: [],
      breakdown: { parts: [{ key: "template", weight: 0.4, value: 0.9 }], damping: null, score: 0.9 },
    },
    {
      gap_id: "gap_demo_risk",
      carried: false,
      coverage: "partial",
      basis: "topic",
      topic_label: "예외 처리",
      topic_centrality: 0.22,
      keywords: ["리스크", "위험", "예외", "실패", "장애", "롤백", "대비"],
      matched_keywords: [],
      evidence: [
        { utterance_id: "utt_demo_1", start_sec: 754, text: "예외 처리는 일단 기존 방식대로 가죠." },
        { utterance_id: "utt_demo_2", start_sec: 1210, text: "실패하면 그때 다시 보면 될 것 같아요." },
      ],
      breakdown: {
        parts: [
          { key: "template", weight: 0.4, value: 0.8 },
          { key: "coverage", weight: 0.4, value: 0.78 },
          { key: "participation", weight: 0.2, value: 0.7 },
        ],
        damping: 0.7,
        score: 0.54,
      },
    },
    {
      gap_id: "gap_demo_dependency",
      carried: false,
      coverage: "partial",
      basis: "topic",
      topic_label: "연동 일정",
      topic_centrality: 0.19,
      keywords: ["의존", "선행", "전제", "필요", "블로커", "대기", "연동"],
      matched_keywords: [],
      evidence: [
        { utterance_id: "utt_demo_3", start_sec: 1502, text: "연동 일정은 결제팀이랑 맞춰봐야 해요." },
      ],
      breakdown: {
        parts: [
          { key: "template", weight: 0.4, value: 0.7 },
          { key: "coverage", weight: 0.4, value: 0.81 },
          { key: "participation", weight: 0.2, value: 0.625 },
        ],
        damping: 0.7,
        score: 0.51,
      },
    },
    {
      gap_id: "gap_demo_next_step",
      carried: false,
      coverage: "partial",
      basis: "topic",
      topic_label: "후속 작업",
      topic_centrality: 0.15,
      keywords: ["다음", "후속", "이후", "계획", "단계", "진행"],
      matched_keywords: [],
      evidence: [
        { utterance_id: "utt_demo_4", start_sec: 2405, text: "후속 작업은 다음에 정리해서 공유할게요." },
      ],
      breakdown: {
        parts: [
          { key: "template", weight: 0.4, value: 0.7 },
          { key: "coverage", weight: 0.4, value: 0.85 },
          { key: "participation", weight: 0.2, value: 0 },
        ],
        damping: 0.7,
        score: 0.43,
      },
    },
  ],
  covered: [
    {
      item_key: "success_criteria",
      topic_label: "성능 목표",
      topic_centrality: 0.78,
      evidence: [
        { utterance_id: "utt_demo_0", start_sec: 512, text: "성능 목표는 p95 300ms로 잡겠습니다." },
      ],
    },
  ],
};
