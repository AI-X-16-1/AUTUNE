import { describe, expect, it } from "vitest";

import { meetingLabel } from "./meetingLabel";
import type { PendingAction } from "./types";

const BASE: PendingAction = {
  id: "pend_1",
  team_id: "team_1",
  meeting_id: "mtg_1",
  subagent: "report",
  kind: "meeting_report_post",
  tool: "intelligence.publish_meeting_report",
  status: "pending",
  reject_reason: null,
  result_ok: null,
  created_at: "2026-10-03T00:00:00Z",
  decided_at: null,
  title: "회의 리포트 게시",
  body: "",
  needs_check: false,
};

describe("meetingLabel", () => {
  it("names the meeting and the day it was held, in Korea's time", () => {
    // 23:30 UTC on 10/1 is 08:30 on Friday 10/2 in Seoul.
    expect(
      meetingLabel({
        ...BASE,
        meeting_title: "주간 회의",
        meeting_started_at: "2026-10-01T23:30:00Z",
      }),
    ).toBe("주간 회의 · 10월 2일(금)");
  });

  it("keeps the title alone when the meeting has no start time", () => {
    expect(
      meetingLabel({
        ...BASE,
        meeting_title: "주간 회의",
        meeting_started_at: null,
      }),
    ).toBe("주간 회의");
  });

  it("falls back to the plain link text without a title", () => {
    expect(meetingLabel(BASE)).toBe("회의 보기");
  });
});
