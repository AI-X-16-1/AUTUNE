import type { RejectReason } from "./types";

/** What a card calls the subagent that proposed it. */
export const SUBAGENT_LABEL: Record<string, string> = {
  research: "리서치",
  workload: "업무 분배",
  followup: "후속 회의",
  report: "리포트",
  tracker: "할 일 챙김",
};

/** The fixed reasons an approver rejects with, in the order they are offered. */
export const REASONS: { value: RejectReason; label: string }[] = [
  { value: "wrong_evidence", label: "근거가 틀림" },
  { value: "not_now", label: "지금은 아님" },
  { value: "handled_elsewhere", label: "다른 곳에서 처리함" },
  { value: "other", label: "기타" },
];
