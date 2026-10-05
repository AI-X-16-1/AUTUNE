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
