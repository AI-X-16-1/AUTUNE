import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { PendingAction } from "../types";
import { ChatProposal } from "./ChatProposal";

// #571: an approver reads a report post's draft on the dashboard card, which
// opens on `/dashboard#report-<meeting id>` (#642).

afterEach(cleanup);

function proposal(
  tool: string,
  meeting_id: string | null = "mtg_abc123",
): PendingAction {
  return {
    id: "pend_1",
    team_id: "team_1",
    meeting_id,
    subagent: "report",
    kind: "meeting_report_post",
    tool,
    status: "pending",
    reject_reason: null,
    result_ok: null,
    created_at: "2026-10-02T00:00:00Z",
    decided_at: null,
    title: "리포트 게시",
    body: "초안 본문",
    needs_check: false,
  };
}

describe("ChatProposal", () => {
  it("links a report post to its report on the dashboard", () => {
    render(
      <ChatProposal item={proposal("intelligence.publish_meeting_report")} />,
    );

    const link = screen.getByRole("link", { name: "대시보드에서 리포트 보기" });
    expect(link.getAttribute("href")).toBe("/dashboard#report-mtg_abc123");
  });

  it("draws no report link for any other proposal or without a meeting", () => {
    render(<ChatProposal item={proposal("extraction.reassign_action_item")} />);
    render(
      <ChatProposal
        item={proposal("intelligence.publish_meeting_report", null)}
      />,
    );

    expect(
      screen.queryByRole("link", { name: "대시보드에서 리포트 보기" }),
    ).toBeNull();
  });
});
