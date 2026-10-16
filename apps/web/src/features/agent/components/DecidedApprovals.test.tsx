import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { DecidedAction } from "../types";
import { ApprovalsScreen } from "./ApprovalsScreen";
import { decidedResult, decidedWhen } from "./DecidedApprovals";

// #1215: a second tab on /approvals lists what was decided and how it went.

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const BASE: DecidedAction = {
  id: "pend_1",
  team_id: "team_1",
  meeting_id: null,
  subagent: "workload",
  kind: "reassign",
  tool: "extraction.reassign_action_item",
  status: "approved",
  reject_reason: null,
  result_ok: true,
  created_at: "2026-10-10T00:00:00Z",
  decided_at: "2026-10-11T05:05:00Z",
  title: "할 일 재배정",
  body: "API 문서 → 박지영",
  needs_check: false,
  decided_by_name: "팀장",
};

describe("decidedResult", () => {
  it("says what happened to each kind of decision", () => {
    expect(decidedResult(BASE).label).toBe("승인됨 · 실행됨");
    expect(
      decidedResult({ ...BASE, status: "failed", result_ok: false }).label,
    ).toBe("승인됨 · 실행 못 함");
    expect(
      decidedResult({ ...BASE, result_ok: null, needs_check: true }).label,
    ).toMatch(/^확인 필요/);
    expect(
      decidedResult({
        ...BASE,
        status: "rejected",
        result_ok: null,
        reject_reason: "not_now",
      }).label,
    ).toBe("거절됨 · 지금은 아님");
  });
});

describe("decidedWhen", () => {
  it("reads the time in Korea", () => {
    expect(decidedWhen("2026-10-11T05:05:00Z")).toBe("10월 11일(일) 14:05");
  });
});

describe("ApprovalsScreen tabs", () => {
  it("reads the decided list only when its tab is opened", async () => {
    vi.spyOn(api, "listPending").mockResolvedValue([]);
    const decided = vi.spyOn(api, "listDecided").mockResolvedValue([BASE]);

    render(<ApprovalsScreen teamId="team_1" />);
    expect(decided).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("tab", { name: "처리한 승인" }));

    expect(await screen.findByText("승인됨 · 실행됨")).toBeTruthy();
    expect(screen.getByText("할 일 재배정")).toBeTruthy();
    expect(screen.getByText(/팀장 · 10월 11일\(일\) 14:05/)).toBeTruthy();
  });

  it("says when nothing was decided lately", async () => {
    vi.spyOn(api, "listPending").mockResolvedValue([]);
    vi.spyOn(api, "listDecided").mockResolvedValue([]);

    render(<ApprovalsScreen teamId="team_1" />);
    fireEvent.click(screen.getByRole("tab", { name: "처리한 승인" }));

    expect(
      await screen.findByText("최근 30일 동안 이 팀에서 처리한 제안이 없습니다."),
    ).toBeTruthy();
  });
});

describe("ApprovalsScreen, one team at a time", () => {
  // 승인 대기 lists the team chosen in the sidebar, not every team the person
  // approves for (the user, 2026-10-13).
  it("lists only the chosen team's waiting proposals", async () => {
    const other = { ...BASE, id: "pend_other", team_id: "team_2" };
    vi.spyOn(api, "listPending").mockResolvedValue([
      { ...BASE, status: "pending", title: "우리 팀 제안" },
      { ...other, status: "pending", title: "다른 팀 제안" },
    ]);

    render(<ApprovalsScreen teamId="team_1" />);

    expect(await screen.findByText("우리 팀 제안")).toBeTruthy();
    expect(screen.queryByText("다른 팀 제안")).toBeNull();
  });

  it("asks the server for the chosen team's decided list", async () => {
    // The list is capped at 50 across what it covers: filtering every team's
    // 50 here let other teams crowd this one out (#1322 review).
    vi.spyOn(api, "listPending").mockResolvedValue([]);
    const decided = vi.spyOn(api, "listDecided").mockResolvedValue([BASE]);

    render(<ApprovalsScreen teamId="team_1" />);
    fireEvent.click(screen.getByRole("tab", { name: "처리한 승인" }));

    expect(await screen.findByText("할 일 재배정")).toBeTruthy();
    expect(decided).toHaveBeenCalledWith("team_1");
  });

  it("reads the decided list again for a newly chosen team", async () => {
    vi.spyOn(api, "listPending").mockResolvedValue([]);
    const decided = vi
      .spyOn(api, "listDecided")
      .mockResolvedValueOnce([BASE])
      .mockResolvedValueOnce([]);

    const { rerender } = render(<ApprovalsScreen teamId="team_1" />);
    fireEvent.click(screen.getByRole("tab", { name: "처리한 승인" }));
    expect(await screen.findByText("할 일 재배정")).toBeTruthy();

    rerender(<ApprovalsScreen teamId="team_2" />);

    expect(
      await screen.findByText("최근 30일 동안 이 팀에서 처리한 제안이 없습니다."),
    ).toBeTruthy();
    expect(decided).toHaveBeenLastCalledWith("team_2");
    expect(screen.queryByText("할 일 재배정")).toBeNull();
  });

  it("reads nothing until the team is known", () => {
    const listed = vi.spyOn(api, "listPending").mockResolvedValue([]);

    render(<ApprovalsScreen teamId={undefined} />);

    expect(screen.getByText("불러오는 중…")).toBeTruthy();
    expect(listed).not.toHaveBeenCalled();
  });

  it("says so to somebody on no team instead of loading for good", () => {
    const listed = vi.spyOn(api, "listPending").mockResolvedValue([]);

    render(<ApprovalsScreen teamId={null} />);

    expect(screen.getByText(/속한 팀이 없습니다/)).toBeTruthy();
    expect(screen.queryByText("불러오는 중…")).toBeNull();
    expect(listed).not.toHaveBeenCalled();
  });
});
