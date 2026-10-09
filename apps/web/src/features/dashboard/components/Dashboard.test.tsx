import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DashboardRead } from "../types";

import { Dashboard } from "./Dashboard";

// #1179: a team with no scored meeting sees one card saying so, not seven
// widgets each saying they are empty.

const state = vi.hoisted(() => ({ dashboard: null as DashboardRead | null }));

vi.mock("../hooks/useDashboard", () => ({
  useDashboard: () => ({
    dashboard: state.dashboard,
    heatmap: [],
    gapTitles: {},
    predictions: null,
    loading: false,
    error: null,
  }),
}));
vi.mock("./MeetingReportsCard", () => ({ MeetingReportsCard: () => <p>회의 리포트 카드</p> }));
vi.mock("./WeeklyReportScheduleCard", () => ({
  WeeklyReportScheduleCard: () => <p>주간 리포트 카드</p>,
}));

function dashboard(meetingCount: number): DashboardRead {
  return {
    team_id: "team_1",
    meeting_count: meetingCount,
    average_score: meetingCount ? 0.8 : null,
    average_grade: meetingCount ? "B" : null,
    action_item_completion_rate: null,
    action_completion_meeting_count: null,
    overdue_action_items: null,
    action_progress_as_of: null,
    action_item_confirmation_rate: null,
    recent_scores: [],
    gap_distribution: {},
  };
}

afterEach(cleanup);

describe("Dashboard", () => {
  it("shows one guide and the weekly schedule before any meeting is scored", () => {
    state.dashboard = dashboard(0);

    render(<Dashboard teamId="team_1" />);

    expect(screen.getByText("아직 분석된 회의가 없습니다.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "회의 시작하기" }).getAttribute("href")).toBe(
      "/meetings/new",
    );
    expect(screen.getByText("주간 리포트 카드")).toBeTruthy();
    expect(screen.queryByText("품질 점수")).toBeNull();
    expect(screen.queryByText("예측")).toBeNull();
    expect(screen.queryByText("회의 리포트 카드")).toBeNull();
  });

  it("shows every widget once a meeting is scored", () => {
    state.dashboard = dashboard(1);

    render(<Dashboard teamId="team_1" />);

    expect(screen.queryByText("아직 분석된 회의가 없습니다.")).toBeNull();
    expect(screen.getByText("품질 점수")).toBeTruthy();
    expect(screen.getByText("예측")).toBeTruthy();
    expect(screen.getByText("회의 리포트 카드")).toBeTruthy();
    expect(screen.getByText("주간 리포트 카드")).toBeTruthy();
  });
});
