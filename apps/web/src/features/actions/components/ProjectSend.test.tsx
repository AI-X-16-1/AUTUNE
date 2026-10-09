import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectSend } from "./ProjectSend";
import type { ProjectSendReport, SendTarget } from "../types";

// "프로젝트별로 보내기" (the user, 2026-10-04): the chosen tools, and per
// project and tool what went.

const send =
  vi.fn<(id: string, targets: SendTarget[]) => Promise<ProjectSendReport>>();
vi.mock("../api", () => ({
  sendSummaryProjects: (id: string, targets: SendTarget[]) => send(id, targets),
}));

afterEach(() => {
  cleanup();
  send.mockReset();
});

describe("ProjectSend", () => {
  it("says why a copy the outbound check refused did not go, not that it failed", async () => {
    send.mockResolvedValue({
      results: [
        { project_id: "prj_a", project_name: "Autune", target: "slack", outcome: "held" },
        { project_id: "prj_b", project_name: "App", target: "slack", outcome: "created" },
      ],
      unsorted: 0,
    });
    render(<ProjectSend meetingId="mtg_1" />);

    fireEvent.click(
      screen.getByRole("button", { name: "프로젝트별로 보내기" }),
    );

    const held = await screen.findByText(/Autune · Slack/);
    expect(held.textContent).toContain("개인정보로 보이는 값이 있어 보내지 않았습니다");
    expect(held.textContent).toContain("문장을 고친 뒤 다시 보내 주세요");
    expect(held.textContent).not.toContain("실패");
    expect(screen.getByText(/App · Slack 보냄/)).toBeTruthy();
  });

  it("sends to the tools left checked and says what went", async () => {
    send.mockResolvedValue({
      results: [
        {
          project_id: "prj_a",
          project_name: "Autune",
          target: "notion",
          outcome: "created",
        },
        {
          project_id: "prj_a",
          project_name: "Autune",
          target: "jira",
          outcome: "not_connected",
        },
      ],
      unsorted: 2,
    });
    render(<ProjectSend meetingId="mtg_1" />);

    fireEvent.click(screen.getByLabelText("Slack"));
    fireEvent.click(
      screen.getByRole("button", { name: "프로젝트별로 보내기" }),
    );

    expect(await screen.findByText(/Autune · Notion 보냄/)).toBeTruthy();
    expect(screen.getByText(/Autune · Jira 연결 안 됨/)).toBeTruthy();
    expect(screen.getByText(/미분류 2건은 보내지 않았습니다/)).toBeTruthy();
    expect(send).toHaveBeenCalledWith("mtg_1", ["notion", "jira"]);
  });

  it("cannot send to nothing", () => {
    render(<ProjectSend meetingId="mtg_1" />);
    for (const label of ["Notion", "Slack", "Jira"]) {
      fireEvent.click(screen.getByLabelText(label));
    }

    const button = screen.getByRole("button", { name: "프로젝트별로 보내기" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("ProjectSend calendar", () => {
  it("adds the sender's own calendar when checked", async () => {
    send.mockResolvedValue({
      results: [
        {
          project_id: "prj_a",
          project_name: "Autune",
          target: "calendar",
          outcome: "no_date",
        },
      ],
      unsorted: 0,
    });
    render(<ProjectSend meetingId="mtg_1" />);

    fireEvent.click(screen.getByLabelText("내 Google 캘린더"));
    fireEvent.click(
      screen.getByRole("button", { name: "프로젝트별로 보내기" }),
    );

    expect(
      await screen.findByText(/Autune · 내 Google 캘린더 회의 날짜 없음/),
    ).toBeTruthy();
    expect(send).toHaveBeenCalledWith("mtg_1", [
      "notion",
      "slack",
      "jira",
      "calendar",
    ]);
  });
});
