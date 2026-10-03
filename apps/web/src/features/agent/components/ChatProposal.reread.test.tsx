import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import * as api from "../api";
import type { PendingAction } from "../types";
import { ChatProposal } from "./ChatProposal";

// Spec 6.2 (agent/docs/specs/2026-10-02-assistant-questions-design.md): a 409,
// or a lost response on 승인, re-reads the queue instead of guessing -- the way
// 승인 대기 does.

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const ITEM: PendingAction = {
  id: "pend_1",
  team_id: "team_1",
  meeting_id: "mtg_abc123",
  subagent: "workload",
  kind: "reassign",
  tool: "extraction.reassign_action_item",
  status: "pending",
  reject_reason: null,
  result_ok: null,
  created_at: "2026-10-02T00:00:00Z",
  decided_at: null,
  title: "액션아이템 재배정",
  body: "API 문서 → 박지영",
  needs_check: false,
};

async function approveAgainst(queue: PendingAction[]): Promise<void> {
  vi.spyOn(api, "approvePending").mockRejectedValue(
    new ApiError(409, "conflict", "decided"),
  );
  vi.spyOn(api, "listPending").mockResolvedValue(queue);
  render(<ChatProposal item={ITEM} />);
  fireEvent.click(screen.getByRole("button", { name: "승인" }));
  await waitFor(() => expect(api.listPending).toHaveBeenCalled());
}

describe("ChatProposal after a 409", () => {
  it("says the proposal is gone when the queue no longer has it", async () => {
    await approveAgainst([]);

    expect(await screen.findByText("더 이상 없는 제안입니다")).toBeTruthy();
  });

  it("asks for a check when the queue marks it so", async () => {
    await approveAgainst([{ ...ITEM, needs_check: true }]);

    expect(await screen.findByText(/직접 확인해 주세요/)).toBeTruthy();
  });

  it("offers the buttons again when it is still pending", async () => {
    await approveAgainst([ITEM]);

    expect(await screen.findByRole("button", { name: "승인" })).toBeTruthy();
    expect(screen.queryByText("더 이상 없는 제안입니다")).toBeNull();
  });
});
