import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DashboardRead } from "../types";

import { useDashboard } from "./useDashboard";

// The dashboard reloads when its tab comes back into view: a person who moved
// cards on the action board in another tab sees the new rate without F5, which
// would also clear the agent chat.

const api = vi.hoisted(() => ({
  getDashboard: vi.fn(),
  getGapTitles: vi.fn(),
  getPredictions: vi.fn(),
}));

vi.mock("../api", () => api);

function dashboard(meetingCount: number): DashboardRead {
  return {
    team_id: "team_1",
    meeting_count: meetingCount,
    average_score: null,
    average_grade: null,
    action_item_completion_rate: null,
    action_completion_meeting_count: null,
    overdue_action_items: null,
    action_progress_as_of: null,
    action_item_confirmation_rate: null,
    recent_scores: [],
    gap_distribution: {},
  };
}

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  api.getDashboard.mockResolvedValue(dashboard(1));
  api.getGapTitles.mockResolvedValue({});
  api.getPredictions.mockResolvedValue(null);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
});

describe("useDashboard", () => {
  it("loads again when the tab comes back into view, not when it is hidden", async () => {
    const { result } = renderHook(() => useDashboard("team_1"));
    await waitFor(() => expect(result.current.dashboard?.meeting_count).toBe(1));
    expect(api.getDashboard).toHaveBeenCalledTimes(1);

    api.getDashboard.mockResolvedValue(dashboard(2));
    act(() => setVisibility("hidden"));
    expect(api.getDashboard).toHaveBeenCalledTimes(1);

    act(() => setVisibility("visible"));
    await waitFor(() => expect(result.current.dashboard?.meeting_count).toBe(2));
    expect(api.getDashboard).toHaveBeenCalledTimes(2);
  });

  it("keeps the newest answer when an older load lands after it", async () => {
    let releaseFirst: (value: DashboardRead) => void = () => {};
    api.getDashboard.mockReturnValueOnce(
      new Promise<DashboardRead>((resolve) => {
        releaseFirst = resolve;
      }),
    );
    api.getDashboard.mockResolvedValueOnce(dashboard(2));

    const { result } = renderHook(() => useDashboard("team_1"));
    await act(() => result.current.reload());
    expect(result.current.dashboard?.meeting_count).toBe(2);

    await act(async () => releaseFirst(dashboard(1)));
    expect(result.current.dashboard?.meeting_count).toBe(2);
  });
});
