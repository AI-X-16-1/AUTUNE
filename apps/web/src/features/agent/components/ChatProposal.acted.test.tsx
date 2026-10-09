import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { onAgentActed } from "@/shared/lib/agentActed";

import * as api from "../api";
import type { PendingAction } from "../types";
import { ChatProposal } from "./ChatProposal";

// #1055: an L2 approved on a chat card ran, so the screen behind reads its
// values again; a rejection changed nothing there.

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const ITEM: PendingAction = {
  id: "pend_1",
  team_id: "team_1",
  meeting_id: "mtg_abc123",
  subagent: "report",
  kind: "meeting_report_post",
  tool: "intelligence.publish_meeting_report",
  status: "pending",
  reject_reason: null,
  result_ok: null,
  created_at: "2026-10-09T00:00:00Z",
  decided_at: null,
  title: "회의 리포트 게시",
  body: "초안",
  needs_check: false,
};

describe("ChatProposal and the page behind it", () => {
  it("tells the page when an approval ran", async () => {
    const heard = vi.fn();
    const stop = onAgentActed(heard);
    vi.spyOn(api, "approvePending").mockResolvedValue({
      ...ITEM,
      status: "approved",
      result_ok: true,
    });

    render(<ChatProposal item={ITEM} />);
    fireEvent.click(screen.getByRole("button", { name: "승인" }));

    expect(await screen.findByText("승인했습니다")).toBeTruthy();
    expect(heard).toHaveBeenCalledTimes(1);
    stop();
  });

  it("says nothing to the page when the approval failed", async () => {
    const heard = vi.fn();
    const stop = onAgentActed(heard);
    vi.spyOn(api, "approvePending").mockResolvedValue({
      ...ITEM,
      status: "failed",
      result_ok: false,
    });

    render(<ChatProposal item={ITEM} />);
    fireEvent.click(screen.getByRole("button", { name: "승인" }));

    expect(await screen.findByText("실행하지 못했습니다")).toBeTruthy();
    expect(heard).not.toHaveBeenCalled();
    stop();
  });
});
