import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { useMeetings, type MeetingsState } from "./useMeetings";
import type { MeetingSummary } from "../types";

// One team's meetings at a time (#839). The hook must never hand out one
// team's list while it is being asked about another -- not even for the one
// render between the team changing and the request for it starting.

const listMeetings = vi.fn<(teamId?: string) => Promise<MeetingSummary[]>>();
vi.mock("../api", () => ({ listMeetings: (teamId?: string) => listMeetings(teamId) }));

afterEach(() => {
  cleanup();
  listMeetings.mockReset();
});

function meeting(id: string, title: string): MeetingSummary {
  return { meeting_id: id, title, status: "complete", started_at: null };
}

const SEARCH = [meeting("mtg_s1", "검색 주간 회의")];
const PAY = [meeting("mtg_p1", "결제 장애 회고")];

function titles(state: MeetingsState): string[] {
  return state.status === "ready" ? state.meetings.map((m) => m.title) : [];
}

describe("useMeetings", () => {
  it("never returns the last team's list for the next team, on any render", async () => {
    listMeetings.mockImplementation((teamId) =>
      Promise.resolve(teamId === "team_search" ? SEARCH : PAY),
    );
    const seen: [string, string[]][] = [];
    const { result, rerender } = renderHook(
      ({ teamId }: { teamId: string }) => {
        const state = useMeetings(teamId);
        seen.push([teamId, titles(state)]);
        return state;
      },
      { initialProps: { teamId: "team_search" } },
    );
    await waitFor(() => expect(titles(result.current)).toEqual(["검색 주간 회의"]));

    rerender({ teamId: "team_pay" });
    await waitFor(() => expect(titles(result.current)).toEqual(["결제 장애 회고"]));

    const underPay = seen.filter(([teamId]) => teamId === "team_pay").map(([, list]) => list);
    expect(underPay.length).toBeGreaterThan(1);
    expect(underPay.flat()).not.toContain("검색 주간 회의");
    expect(underPay[0]).toEqual([]);
  });

  it("is loading again the moment the team changes", async () => {
    listMeetings.mockImplementation((teamId) =>
      teamId === "team_search" ? Promise.resolve(SEARCH) : new Promise(() => {}),
    );
    const { result, rerender } = renderHook(
      ({ teamId }: { teamId: string }) => useMeetings(teamId),
      { initialProps: { teamId: "team_search" } },
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));

    rerender({ teamId: "team_pay" });

    expect(result.current).toEqual({ status: "loading" });
  });

  it("drops an answer that lands for the team that was left", async () => {
    let late: (rows: MeetingSummary[]) => void = () => {};
    listMeetings.mockImplementation((teamId) =>
      teamId === "team_search"
        ? new Promise((resolve) => {
            late = resolve;
          })
        : Promise.resolve(PAY),
    );
    const { result, rerender } = renderHook(
      ({ teamId }: { teamId: string }) => useMeetings(teamId),
      { initialProps: { teamId: "team_search" } },
    );
    rerender({ teamId: "team_pay" });
    await waitFor(() => expect(titles(result.current)).toEqual(["결제 장애 회고"]));

    await act(async () => {
      late(SEARCH);
    });

    expect(titles(result.current)).toEqual(["결제 장애 회고"]);
  });

  it("says so in its own words when the list cannot be read", async () => {
    listMeetings.mockRejectedValue("not an Error");
    const { result } = renderHook(() => useMeetings("team_search"));

    await waitFor(() =>
      expect(result.current).toMatchObject({
        status: "error",
        message: "회의 목록을 불러오지 못했습니다.",
      }),
    );
  });

  // #1176: a 500 behind the proxy is "Internal Server Error", in English.
  it("never hands on a server failure's own text", async () => {
    listMeetings.mockRejectedValue(new ApiError(500, "unknown", "Internal Server Error"));
    const { result } = renderHook(() => useMeetings("team_search"));

    await waitFor(() =>
      expect(result.current).toMatchObject({
        status: "error",
        message: "회의 목록을 불러오지 못했습니다.",
      }),
    );
  });

  it("asks again when retried", async () => {
    listMeetings.mockRejectedValueOnce(new ApiError(500, "unknown", "Internal Server Error"));
    listMeetings.mockResolvedValueOnce(PAY);
    const { result } = renderHook(() => useMeetings("team_pay"));
    await waitFor(() => expect(result.current.status).toBe("error"));

    act(() => {
      if (result.current.status === "error") result.current.retry();
    });

    await waitFor(() => expect(titles(result.current)).toEqual(["결제 장애 회고"]));
    expect(listMeetings).toHaveBeenCalledTimes(2);
  });
});