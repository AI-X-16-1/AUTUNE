import { afterEach, describe, expect, it, vi } from "vitest";

import { JIRA_AGENDA } from "./agendaSources";
import type { JiraIssue, JiraProjectIssues } from "./api";

// Module B's source for the new-meeting form's agenda draft (#1147).

const jira = vi.fn<() => Promise<JiraProjectIssues[]>>();
vi.mock("./api", () => ({ listJiraOpenIssues: () => jira() }));

afterEach(() => jira.mockReset());

const issue = (overrides: Partial<JiraIssue>): JiraIssue => ({
  key: "SRCH-12",
  summary: "검색 응답 시간 개선",
  status: "진행 중",
  status_category: "indeterminate",
  assignee: "김담당",
  due_date: "2026-10-20",
  url: "https://example.atlassian.net/browse/SRCH-12",
  from_autune: false,
  ...overrides,
});
const project = (overrides: Partial<JiraProjectIssues>): JiraProjectIssues => ({
  team_id: "team_1",
  team_name: "검색팀",
  project_key: "SRCH",
  state: "ok",
  issues: [issue({})],
  more: false,
  ...overrides,
});

describe("JIRA_AGENDA", () => {
  it("is the chosen team's issues only: the route answers for every team of the reader", async () => {
    jira.mockResolvedValue([
      project({}),
      project({ team_id: "team_2", issues: [issue({ key: "PAY-3", summary: "결제 재시도" })] }),
    ]);

    expect(await JIRA_AGENDA.lines("team_1")).toEqual([
      { title: "검색 응답 시간 개선", detail: "SRCH-12 · 진행 중" },
    ]);
  });

  it("keeps Jira's order", async () => {
    jira.mockResolvedValue([
      project({ issues: [issue({ key: "SRCH-2", summary: "둘" }), issue({ key: "SRCH-1", summary: "하나" })] }),
    ]);

    expect((await JIRA_AGENDA.lines("team_1")).map((line) => line.title)).toEqual(["둘", "하나"]);
  });

  it("carries the summary, the key and the status, and nothing of a person", async () => {
    jira.mockResolvedValue([project({})]);

    const [line] = await JIRA_AGENDA.lines("team_1");

    expect(Object.keys(line ?? {}).sort()).toEqual(["detail", "title"]);
    expect(JSON.stringify(line)).not.toContain("김담당");
  });

  it("shows the key alone for an issue with no status", async () => {
    jira.mockResolvedValue([project({ issues: [issue({ status: null })] })]);

    expect((await JIRA_AGENDA.lines("team_1"))[0]?.detail).toBe("SRCH-12");
  });

  it("is nothing for a team that connected no Jira, or whose project cannot be read", async () => {
    jira.mockResolvedValue([project({ state: "needs_reconnect", issues: [] })]);

    expect(await JIRA_AGENDA.lines("team_1")).toEqual([]);
    expect(await JIRA_AGENDA.lines("team_without_jira")).toEqual([]);
  });
});
