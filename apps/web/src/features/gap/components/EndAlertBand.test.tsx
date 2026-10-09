import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EndAlertBand } from "./EndAlertBand";
import { END_ALERT } from "../endAlert";
import type { GapSeverity, TeamGap } from "../types";

// S14's small cut (#1147): the band a live recording shows five minutes before
// its planned end. It reads the team's open gaps through the route that exists
// (#550) and says what that list is.

const list = vi.fn<(teamId: string, severities: readonly GapSeverity[]) => Promise<TeamGap[]>>();
vi.mock("../api", () => ({
  listTeamGaps: (teamId: string, severities: readonly GapSeverity[]) => list(teamId, severities),
}));

const row = (overrides: Partial<TeamGap>): TeamGap => ({
  gap_id: "gap_1",
  meeting_id: "mtg_old",
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

const many = (count: number) =>
  Array.from({ length: count }, (_, index) =>
    row({ gap_id: `gap_${index}`, title: `열린 갭 ${index}` }),
  );

afterEach(() => {
  cleanup();
  list.mockReset();
});

describe("EndAlertBand", () => {
  it("asks the existing team list for HIGH alone, once", async () => {
    list.mockResolvedValue([row({})]);
    render(<EndAlertBand teamId="team_1" />);

    await screen.findByRole("status", { name: "종료 5분 전 알림" });
    expect(list).toHaveBeenCalledTimes(1);
    expect(list).toHaveBeenCalledWith("team_1", ["high"]);
  });

  it("says how many gaps earlier meetings left open, and does not call them carried over", async () => {
    list.mockResolvedValue([row({}), row({ gap_id: "gap_2", title: "롤백 계획이 없습니다" })]);
    render(<EndAlertBand teamId="team_1" />);

    const band = await screen.findByRole("status", { name: "종료 5분 전 알림" });
    expect(band.textContent).toContain("종료 5분 전 · 이전 회의의 미해결 갭 2건");
    expect(band.textContent).not.toContain("넘어온");
  });

  it("keeps the list folded until asked, then names each gap and its meeting", async () => {
    list.mockResolvedValue([row({}), row({ gap_id: "gap_2", title: "롤백 계획이 없습니다" })]);
    render(<EndAlertBand teamId="team_1" />);

    await screen.findByRole("status", { name: "종료 5분 전 알림" });
    expect(screen.queryByText("롤백 계획이 없습니다")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "보기" }));

    expect(screen.getByText("성능 요구사항이 정해지지 않았습니다")).toBeTruthy();
    expect(screen.getByText("롤백 계획이 없습니다")).toBeTruthy();
    expect(screen.getAllByText("검색 개편 회의 · 2026-10-03")).toHaveLength(2);
    // The suggested question is S14's "질문으로 띄우기", which this cut leaves out.
    expect(screen.queryByText("응답 시간 목표는 몇 ms입니까?")).toBeNull();
  });

  it("leaves the meeting being recorded out of its own earlier meetings", async () => {
    list.mockResolvedValue([
      row({ gap_id: "gap_1", meeting_id: "mtg_live", title: "이 회의의 갭" }),
      row({ gap_id: "gap_2", meeting_id: "mtg_old", title: "지난 회의의 갭" }),
    ]);
    render(<EndAlertBand teamId="team_1" exceptMeetingId="mtg_live" />);

    const band = await screen.findByRole("status", { name: "종료 5분 전 알림" });
    expect(band.textContent).toContain("미해결 갭 1건");
    fireEvent.click(screen.getByRole("button", { name: "보기" }));
    expect(screen.queryByText("이 회의의 갭")).toBeNull();
    expect(screen.getByText("지난 회의의 갭")).toBeTruthy();
  });

  it("lists five and counts the rest, with the count in the band being of all", async () => {
    list.mockResolvedValue(many(8));
    render(<EndAlertBand teamId="team_1" />);

    const band = await screen.findByRole("status", { name: "종료 5분 전 알림" });
    expect(band.textContent).toContain("미해결 갭 8건");
    fireEvent.click(screen.getByRole("button", { name: "보기" }));

    expect(screen.getAllByRole("listitem")).toHaveLength(5);
    expect(band.textContent).toContain("외 3건");
  });

  it("opens the gap report in a new tab, so the recording tab is not left", async () => {
    list.mockResolvedValue([row({})]);
    render(<EndAlertBand teamId="team_1" />);

    await screen.findByRole("status", { name: "종료 5분 전 알림" });
    fireEvent.click(screen.getByRole("button", { name: "보기" }));

    const link = screen.getByRole("link", { name: "갭 리포트를 새 탭에서 열기" });
    expect(link.getAttribute("href")).toBe("/gaps");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("is gone once closed", async () => {
    list.mockResolvedValue([row({})]);
    const { container } = render(<EndAlertBand teamId="team_1" />);

    await screen.findByRole("status", { name: "종료 5분 전 알림" });
    fireEvent.click(screen.getByRole("button", { name: "닫기" }));

    expect(container.innerHTML).toBe("");
  });

  it("says so in one quiet line when nothing is open, with no band", async () => {
    list.mockResolvedValue([]);
    render(<EndAlertBand teamId="team_1" />);

    const line = await screen.findByRole("status");
    expect(line.textContent).toBe("종료 5분 전 · 이전 회의의 미해결 갭이 없습니다");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says the list could not be read, quietly, and raises no error over the recording", async () => {
    list.mockRejectedValue(new Error("boom"));
    render(<EndAlertBand teamId="team_1" />);

    const line = await screen.findByRole("status");
    expect(line.textContent).toBe("종료 5분 전 · 미해결 갭을 불러오지 못했습니다");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(line.textContent).not.toContain("boom");
  });

  it("draws nothing while the list is on its way", async () => {
    list.mockReturnValue(new Promise(() => undefined));
    const { container } = render(<EndAlertBand teamId="team_1" />);

    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });
});

describe("the band's seam", () => {
  it("holds the list and the sentence together, so they are swapped together", async () => {
    list.mockResolvedValue([]);

    await END_ALERT.list("team_9");

    expect(list).toHaveBeenCalledWith("team_9", ["high"]);
    expect(END_ALERT.sentence(3)).toBe("이전 회의의 미해결 갭 3건");
    expect(Object.keys(END_ALERT).sort()).toEqual(["list", "none", "sentence"]);
  });
});
