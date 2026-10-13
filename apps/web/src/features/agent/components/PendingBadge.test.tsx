import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { announcePendingChanged, resetPendingCounts } from "../pendingCount";
import type { PendingAction } from "../types";
import { PendingBadge } from "./PendingBadge";

afterEach(() => {
  cleanup();
  resetPendingCounts();
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

    render(<PendingBadge pathname="/" teamId="team_1" />);

    expect(await screen.findByText("2")).toBeTruthy();
    expect(screen.getByText(", 승인할 제안 2건")).toBeTruthy();
  });

  it("draws nothing while nothing waits", async () => {
    const listed = vi.spyOn(api, "listPending").mockResolvedValue([]);
    const { container } = render(<PendingBadge pathname="/" teamId="team_1" />);
    await waitFor(() => expect(listed).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("reads the queue again when a decision is announced", async () => {
    const listed = vi
      .spyOn(api, "listPending")
      .mockResolvedValueOnce([row("a")])
      .mockResolvedValueOnce([]);
    const { container } = render(<PendingBadge pathname="/approvals" teamId="team_1" />);
    expect(await screen.findByText("1")).toBeTruthy();

    act(() => announcePendingChanged());

    await waitFor(() => expect(listed).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(container.textContent).toBe(""));
  });
});

describe("PendingBadge, per team", () => {
  it("counts only the chosen team's proposals", async () => {
    vi.spyOn(api, "listPending").mockResolvedValue([
      row("a"),
      row("b", { team_id: "team_2" }),
      row("c", { team_id: "team_2" }),
    ]);

    render(<PendingBadge pathname="/" teamId="team_2" />);

    expect(await screen.findByText("2")).toBeTruthy();
  });

  it("draws nothing while no team is chosen", async () => {
    const listed = vi.spyOn(api, "listPending").mockResolvedValue([row("a")]);
    const { container } = render(<PendingBadge pathname="/" teamId={null} />);
    await waitFor(() => expect(listed).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("reads the queue once for every place that shows a count", async () => {
    const listed = vi.spyOn(api, "listPending").mockResolvedValue([row("a")]);

    render(
      <>
        <PendingBadge pathname="/" teamId="team_1" />
        <PendingBadge pathname="/" teamId="team_1" />
      </>,
    );

    expect(await screen.findAllByText("1")).toHaveLength(2);
    expect(listed).toHaveBeenCalledTimes(1);
  });
});
