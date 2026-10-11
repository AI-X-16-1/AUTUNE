import { describe, expect, it } from "vitest";

import { decisionSourceLine } from "./DecisionReview";

const model = {
  origin: "model" as const,
  summary: "다음 분기에 색인을 다시 만들기로 했습니다",
  confidence: 0.82,
  source_utterance_ids: ["utt_1", "utt_2"],
};

describe("what a decision's row says of its sources", () => {
  it("is the preview and the confidence while every source is there", () => {
    expect(decisionSourceLine(model)).toBe("다음 분기에 색인을 다시 만들기로 했습니다 · 신뢰도 82%");
    expect(decisionSourceLine({ ...model, summary: null, deleted_source_count: 0 })).toBe(
      "근거 발화 2건 · 신뢰도 82%",
    );
  });

  it("says how many were deleted when some are left", () => {
    expect(decisionSourceLine({ ...model, deleted_source_count: 1 })).toBe(
      "다음 분기에 색인을 다시 만들기로 했습니다 · 1건 삭제됨 · 신뢰도 82%",
    );
  });

  it("says the sources were deleted, not that there were none", () => {
    const gone = { ...model, summary: null, source_utterance_ids: [], deleted_source_count: 2 };

    expect(decisionSourceLine(gone)).toBe("근거 발화 삭제됨 · 신뢰도 82%");
  });

  it("tells a hand-added decision that lost its sources from one that had none", () => {
    const added = { origin: "user" as const, summary: null, confidence: 1, source_utterance_ids: [] };

    expect(decisionSourceLine(added)).toBe("직접 추가");
    expect(decisionSourceLine({ ...added, deleted_source_count: 2 })).toBe(
      "직접 추가 · 근거 발화 삭제됨",
    );
    expect(
      decisionSourceLine({ ...added, source_utterance_ids: ["utt_1"], deleted_source_count: 1 }),
    ).toBe("직접 추가 · 근거 발화 1건 삭제됨");
  });
});
