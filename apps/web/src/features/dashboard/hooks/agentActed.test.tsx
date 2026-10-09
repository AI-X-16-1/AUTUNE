import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { announceAgentActed } from "@/shared/lib/agentActed";

import * as api from "../api";
import { MeetingReportsCard } from "../components/MeetingReportsCard";
import type { MeetingReport, WeeklyReportSchedule } from "../types";
import { useMeetingReports } from "./useMeetingReports";
import { useWeeklyReportSchedule } from "./useWeeklyReportSchedule";

// #1055: what the assistant changes (a schedule, a re-draft, an approved post)
// shows on the dashboard without a reload, which would end the conversation;
// and a report row the assistant links to opens while the dashboard is open.

const SCHEDULE: WeeklyReportSchedule = {
  weekday: 0,
  hour: 9,
  send_empty: false,
  updated_by_name: null,
  updated_at: null,
};

function report(meetingId: string, title: string): MeetingReport {
  return {
    meeting_id: meetingId,
    title,
    body: `${title} 본문`,
    footer: "자동 생성된 리포트입니다.",
    status: "draft",
    posted_at: null,
    pending_review: false,
    edited_by_name: null,
    edited_at: null,
    updated_at: "2026-10-09T00:00:00Z",
    in_slack: false,
    correction_body: null,
    corrected_by_name: null,
    corrected_at: null,
    correction_status: null,
  };
}

beforeEach(() => {
  window.history.replaceState(null, "", "/dashboard");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the dashboard hears the assistant", () => {
  it("reads the weekly report schedule again", async () => {
    const read = vi
      .spyOn(api, "getWeeklyReportSchedule")
      .mockResolvedValueOnce(SCHEDULE)
      .mockResolvedValueOnce({ ...SCHEDULE, weekday: 4, hour: 18 });

    const { result } = renderHook(() => useWeeklyReportSchedule("team_1"));
    await waitFor(() => expect(result.current.schedule?.hour).toBe(9));
    act(() => announceAgentActed());

    await waitFor(() => expect(result.current.schedule?.hour).toBe(18));
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("reads the meeting reports again", async () => {
    const read = vi
      .spyOn(api, "getMeetingReports")
      .mockResolvedValueOnce([report("mtg_a1", "첫 초안")])
      .mockResolvedValueOnce([report("mtg_a1", "다시 쓴 초안")]);

    const { result } = renderHook(() => useMeetingReports("team_1"));
    await waitFor(() => expect(result.current.reports[0]?.title).toBe("첫 초안"));
    act(() => announceAgentActed());

    await waitFor(() => expect(result.current.reports[0]?.title).toBe("다시 쓴 초안"));
    expect(read).toHaveBeenCalledTimes(2);
  });
});

describe("the report card follows the hash", () => {
  it("opens the report a new hash names while the card is open", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(api, "getMeetingReports").mockResolvedValue([
      report("mtg_a1", "결제 회의"),
      report("mtg_b2", "배포 회의"),
    ]);

    render(<MeetingReportsCard teamId="team_1" />);
    const row = await screen.findByRole("button", { name: /배포 회의/ });
    expect(row.getAttribute("aria-expanded")).toBe("false");

    act(() => {
      window.history.replaceState(null, "", "/dashboard#report-mtg_b2");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });

    await waitFor(() => expect(row.getAttribute("aria-expanded")).toBe("true"));
  });
});
