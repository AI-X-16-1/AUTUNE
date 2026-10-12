import { describe, expect, it } from "vitest";

import { cardsNotice } from "../hooks/useGapActions";

// "질문 카드 Slack 전송" (#824, plan 3) posts the meeting's open high gaps as
// question cards, a few at most. What the screen says depends on how many went
// before the channel stopped taking them.

const result = (high: number, sent: number, slack: Parameters<typeof cardsNotice>[0]["slack"]) =>
  cardsNotice({ meeting_id: "mtg_1", high, sent, slack });

describe("질문 카드 Slack 전송 — what the screen says", () => {
  it("counts the cards posted and the ones left to the report's link", () => {
    expect(result(2, 2, "posted")).toBe("팀 Slack 채널에 질문 카드 2건을 올렸습니다.");
    expect(result(5, 3, "posted")).toBe(
      "팀 Slack 채널에 질문 카드 3건을 올렸습니다. 나머지 2건은 갭 리포트 링크로 안내했습니다.",
    );
  });

  it("says how many went before Slack stopped taking them", () => {
    expect(result(3, 1, "failed")).toBe(
      "질문 카드 1건을 올린 뒤 팀 Slack 채널에 질문 카드를 올리지 못했습니다. 잠시 후 다시 시도해 주세요.",
    );
    expect(result(3, 0, "refused")).toBe(
      "개인정보로 보이는 내용이 있어 팀 Slack 채널에 더 보내지 않았습니다.",
    );
  });

  it("says why nothing was sent", () => {
    expect(result(0, 0, "not_tried")).toBe("Slack으로 보낼 위험도 높은 갭이 없습니다.");
    expect(result(2, 0, "no_slack")).toBe(
      "팀 Slack 채널이 연결되어 있지 않아 질문 카드를 보내지 못했습니다.",
    );
  });
});
