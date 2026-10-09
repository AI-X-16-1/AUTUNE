import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TeamGapList } from "./TeamGapList";
import type { GapSeverity, TeamGap } from "../types";

// The sidebar's "갭 리포트" (#550): a team's open gaps, grouped by meeting,
// HIGH alone until asked.

const list = vi.fn<(teamId: string, severities: readonly GapSeverity[]) => Promise<TeamGap[]>>();
vi.mock("../api", () => ({
  listTeamGaps: (teamId: string, severities: readonly GapSeverity[]) => list(teamId, severities),
}));

const row = (overrides: Partial<TeamGap>): TeamGap => ({
  gap_id: "gap_1",
  meeting_id: "mtg_new",
  meeting_title: "검색 개편 회의",
  meeting_date: "2026-10-03T09:00:00Z",
  category: "technical_spec",
  title: "성능 요구사항이 정해지지 않았습니다",
  severity: "high",
  risk_score: 0.91,
  template_item: null,
  suggested_question: "응답 시간 목표는 몇 ms입니까?",
  ...overrides,
});

afterEach(() => {
  cleanup();
  list.mockReset();
});

describe("TeamGapList", () => {
  it("asks for HIGH alone by default", async () => {
    list.mockResolvedValue([]);
    render(<TeamGapList teamId="team_1" />);

    await waitFor(() => expect(list).toHaveBeenCalledWith("team_1", ["high"]));
    expect(await screen.findByText(/열린 높음 갭이 없습니다/)).toBeTruthy();
  });

  it("groups rows by meeting and links each meeting to its report", async () => {
    list.mockResolvedValue([
      row({ gap_id: "gap_1" }),
      row({ gap_id: "gap_2", title: "롤백 계획이 없습니다", suggested_question: null }),
      row({
        gap_id: "gap_3",
        meeting_id: "mtg_old",
        meeting_title: "주간 회의",
        meeting_date: "2026-09-28T09:00:00Z",
      }),
    ]);
    render(<TeamGapList teamId="team_1" />);

    const newer = await screen.findByRole("link", { name: /검색 개편 회의/ });
    expect(newer.getAttribute("href")).toBe("/meetings/mtg_new/gap");
    expect(newer.textContent).toContain("2026-10-03 · 2건");
    expect(screen.getByRole("link", { name: /주간 회의/ }).getAttribute("href")).toBe(
      "/meetings/mtg_old/gap",
    );
    expect(screen.getByText("롤백 계획이 없습니다")).toBeTruthy();
    expect(screen.getAllByText("응답 시간 목표는 몇 ms입니까?")).toHaveLength(2);
  });

  it("asks for every severity once toggled", async () => {
    list.mockResolvedValue([]);
    render(<TeamGapList teamId="team_1" />);
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "중간·낮음도 보기" }));

    await waitFor(() => expect(list).toHaveBeenLastCalledWith("team_1", ["high", "medium", "low"]));
    expect(await screen.findByText("열린 갭이 없습니다.")).toBeTruthy();
  });

  it("offers MEDIUM and LOW from the empty HIGH list itself", async () => {
    list.mockResolvedValue([]);
    render(<TeamGapList teamId="team_1" />);

    fireEvent.click(await screen.findByRole("button", { name: "중간·낮음 갭 보기" }));

    await waitFor(() => expect(list).toHaveBeenLastCalledWith("team_1", ["high", "medium", "low"]));
    expect(screen.queryByText(/위에서/)).toBeNull();
  });

  it("says it could not load, and can try again", async () => {
    list.mockRejectedValueOnce(new Error("500")).mockResolvedValue([]);
    render(<TeamGapList teamId="team_1" />);

    fireEvent.click(await screen.findByRole("button", { name: "다시 시도" }));

    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/열린 높음 갭이 없습니다/)).toBeTruthy();
  });
});
