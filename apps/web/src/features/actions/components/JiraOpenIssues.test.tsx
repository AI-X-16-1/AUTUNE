import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JiraOpenIssues } from "./JiraOpenIssues";
import type { JiraProjectIssues } from "../api";

// The team's Jira, viewed beside the board and never imported (the user,
// 2026-10-02). Read only when asked for; what it shows is what Jira said.

const list = vi.fn<() => Promise<JiraProjectIssues[]>>();
vi.mock("../api", () => ({ listJiraOpenIssues: () => list() }));

const PROJECT: JiraProjectIssues = {
  team_id: "team_1",
  team_name: "제품팀",
  project_key: "AUT",
  state: "ok",
  more: false,
  issues: [
    {
      key: "AUT-7",
      summary: "배포 일정 공유",
      status: "진행 중",
      status_category: "indeterminate",
      assignee: "가나다",
      due_date: "2026-10-09",
      url: "https://acme.atlassian.net/browse/AUT-7",
      from_autune: true,
    },
    {
      key: "AUT-8",
      summary: "미정",
      status: null,
      status_category: null,
      assignee: null,
      due_date: null,
      url: null,
      from_autune: false,
    },
  ],
};

const open = () => fireEvent.click(screen.getByRole("button", { name: "Jira 열린 이슈 보기" }));

afterEach(() => {
  cleanup();
  list.mockReset();
});

describe("JiraOpenIssues", () => {
  it("asks Jira nothing until it is opened", () => {
    render(<JiraOpenIssues />);

    expect(list).not.toHaveBeenCalled();
  });

  it("shows each open issue with its link, and says it is not stored", async () => {
    list.mockResolvedValue([PROJECT]);
    render(<JiraOpenIssues />);

    open();

    const link = (await screen.findByRole("link", { name: "AUT-7" })) as HTMLAnchorElement;
    expect(link.href).toBe("https://acme.atlassian.net/browse/AUT-7");
    expect(link.rel).toBe("noopener noreferrer");
    expect(screen.getByText("배포 일정 공유")).toBeTruthy();
    expect(screen.getByText("진행 중 · 가나다 · 2026-10-09")).toBeTruthy();
    expect(screen.getByText("Autune에서 만든 이슈")).toBeTruthy();
    expect(screen.getByText("제품팀 · AUT")).toBeTruthy();
    expect(screen.getByText(/Autune에 저장하지 않습니다/)).toBeTruthy();
    // No link where the server gave none, and the gaps are named.
    expect(screen.queryByRole("link", { name: "AUT-8" })).toBeNull();
    expect(screen.getByText("상태 없음 · 담당자 없음 · 기한 없음")).toBeTruthy();
  });

  it.each([
    ["no_project", /프로젝트를 아직 고르지 않았습니다/],
    ["needs_reconnect", /다시 연결해 주세요/],
    ["unavailable", /Jira가 응답하지 않습니다/],
  ] as const)("says why a project has no list: %s", async (state, message) => {
    list.mockResolvedValue([{ ...PROJECT, state, issues: [] }]);
    render(<JiraOpenIssues />);

    open();

    expect(await screen.findByText(message)).toBeTruthy();
  });

  it("says so when no team has connected a project", async () => {
    list.mockResolvedValue([]);
    render(<JiraOpenIssues />);

    open();

    expect(await screen.findByText(/연결한 Jira 프로젝트가 없습니다/)).toBeTruthy();
  });

  it("says so when Jira has more than it read", async () => {
    list.mockResolvedValue([{ ...PROJECT, more: true }]);
    render(<JiraOpenIssues />);

    open();

    expect(await screen.findByText(/일부만 보여 줍니다/)).toBeTruthy();
  });

  it("says so when the request failed, and reads again when asked", async () => {
    list.mockRejectedValueOnce(new Error("502"));
    list.mockResolvedValue([PROJECT]);
    render(<JiraOpenIssues />);

    open();
    expect((await screen.findByRole("alert")).textContent).toContain("불러오지 못했습니다");
    fireEvent.click(screen.getByRole("button", { name: "다시 불러오기" }));

    await screen.findByRole("link", { name: "AUT-7" });
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("drops what it read when closed", async () => {
    list.mockResolvedValue([PROJECT]);
    render(<JiraOpenIssues />);

    open();
    await screen.findByRole("link", { name: "AUT-7" });
    fireEvent.click(screen.getByRole("button", { name: "접기" }));

    await waitFor(() => expect(screen.queryByText("배포 일정 공유")).toBeNull());
  });

  it("stays closed when the answer arrives after it was closed", async () => {
    let answer: (projects: JiraProjectIssues[]) => void = () => {};
    list.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    render(<JiraOpenIssues />);

    open();
    await screen.findByText("Jira에서 불러오는 중입니다.");
    fireEvent.click(screen.getByRole("button", { name: "접기" }));
    await act(async () => answer([PROJECT]));

    expect(screen.queryByText("배포 일정 공유")).toBeNull();
    expect(screen.getByRole("button", { name: "Jira 열린 이슈 보기" })).toBeTruthy();
  });

  it("stays closed when a request that failed is answered after it was closed", async () => {
    let fail: (reason: Error) => void = () => {};
    list.mockReturnValue(new Promise((_, reject) => (fail = reject)));
    render(<JiraOpenIssues />);

    open();
    await screen.findByText("Jira에서 불러오는 중입니다.");
    fireEvent.click(screen.getByRole("button", { name: "접기" }));
    await act(async () => fail(new Error("502")));

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "Jira 열린 이슈 보기" })).toBeTruthy();
  });
});
