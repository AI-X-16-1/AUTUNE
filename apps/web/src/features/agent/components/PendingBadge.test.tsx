import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { announcePendingChanged } from "../pendingCount";
import type { PendingAction } from "../types";
import { PendingBadge } from "./PendingBadge";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const row = (
  id: string,
  extra: Partial<PendingAction> = {},
): PendingAction => ({
  id,
  team_id: "team_1",
  meeting_id: null,
  subagent: "workload",
  kind: "reassign",
  tool: "extraction.reassign_action_item",
  status: "pending",
  reject_reason: null,
  result_ok: null,
  created_at: "2026-10-03T00:00:00Z",
  decided_at: null,
  title: "재배정",
  body: "",
  needs_check: false,
  ...extra,
});

describe("PendingBadge", () => {
  it("counts only what still waits", async () => {
    vi.spyOn(api, "listPending").mockResolvedValue([
      row("a"),
      row("b"),
      row("c", { status: "approved", needs_check: true }),
    ]);

    render(<PendingBadge pathname="/" />);

    expect(await screen.findByText("2")).toBeTruthy();
    expect(screen.getByText(", 승인할 제안 2건")).toBeTruthy();
  });

  it("draws nothing while nothing waits", async () => {
    const listed = vi.spyOn(api, "listPending").mockResolvedValue([]);
    const { container } = render(<PendingBadge pathname="/" />);
    await waitFor(() => expect(listed).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("reads the queue again when a decision is announced", async () => {
    const listed = vi
      .spyOn(api, "listPending")
      .mockResolvedValueOnce([row("a")])
      .mockResolvedValueOnce([]);
    const { container } = render(<PendingBadge pathname="/approvals" />);
    expect(await screen.findByText("1")).toBeTruthy();

    act(() => announcePendingChanged());

    await waitFor(() => expect(listed).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(container.textContent).toBe(""));
  });
});
