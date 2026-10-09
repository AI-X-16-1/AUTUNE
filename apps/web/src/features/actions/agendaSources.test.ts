import { afterEach, describe, expect, it, vi } from "vitest";

import { EARLIER_ITEMS_AGENDA, JIRA_AGENDA } from "./agendaSources";
import type { ActionItemFilter, JiraIssue, JiraProjectIssues } from "./api";
import type { ActionItemRead } from "./types";

// Module B's sources for an agenda draft (#1147): the team's open Jira issues
// for the new-meeting form, an earlier meeting's unfinished to-dos for the brief.

const jira = vi.fn<() => Promise<JiraProjectIssues[]>>();
const items = vi.fn<(filter: ActionItemFilter) => Promise<ActionItemRead[]>>();
vi.mock("./api", () => ({
  listJiraOpenIssues: () => jira(),
  listActionItems: (filter: ActionItemFilter) => items(filter),
}));

afterEach(() => {
  jira.mockReset();
  items.mockReset();
});

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

const item = (overrides: Partial<ActionItemRead>): ActionItemRead =>
  ({
    id: "act_1",
    meeting_id: "mtg_earlier",
    title: "색인 재구축",
    description: "검색 색인을 다음 주까지 다시 만든다",
    status: "todo",
    assignee_id: "user_kim",
    assignee_name: "김담당",
    due_date: "2026-10-01",
    ...overrides,
  }) as ActionItemRead;

describe("EARLIER_ITEMS_AGENDA", () => {
  it("asks for the one meeting the brief named, and nothing wider", async () => {
    items.mockResolvedValue([]);

    await EARLIER_ITEMS_AGENDA.lines("mtg_earlier");

    expect(items.mock.calls).toEqual([[{ meeting_id: "mtg_earlier" }]]);
  });

  it("keeps what is not finished, in the server's order: 진행 전 and 진행 중", async () => {
    items.mockResolvedValue([
      item({ id: "act_1", title: "하나", status: "in_progress" }),
      item({ id: "act_2", title: "확인 전", status: "needs_confirmation" }),
      item({ id: "act_3", title: "끝난 일", status: "done" }),
      item({ id: "act_4", title: "둘", status: "todo" }),
      item({ id: "act_5", title: "상태 없음", status: undefined }),
    ]);

    expect(await EARLIER_ITEMS_AGENDA.lines("mtg_earlier")).toEqual([{ title: "하나" }, { title: "둘" }]);
  });

  it("is the title alone: no assignee, no due date, nothing under the line", async () => {
    items.mockResolvedValue([item({})]);

    const [line] = await EARLIER_ITEMS_AGENDA.lines("mtg_earlier");

    expect(Object.keys(line ?? {})).toEqual(["title"]);
    expect(JSON.stringify(line)).not.toContain("김담당");
    expect(JSON.stringify(line)).not.toContain("2026-10-01");
    expect(JSON.stringify(line)).not.toContain("user_kim");
  });

  it("draws the line the board draws: the written title, or the sentence cut when there is none", async () => {
    items.mockResolvedValue([
      item({ title: "색인 재구축" }),
      item({ id: "act_2", title: null, description: "검색 색인을 다음 주 화요일까지 다시 만들고 결과를 공유한다" }),
    ]);

    const lines = await EARLIER_ITEMS_AGENDA.lines("mtg_earlier");

    expect(lines[0]).toEqual({ title: "색인 재구축" });
    expect(lines[1]?.title.endsWith("…")).toBe(true);
    expect([...(lines[1]?.title ?? "")].length).toBeLessThanOrEqual(20);
    expect("검색 색인을 다음 주 화요일까지 다시 만들고 결과를 공유한다".startsWith((lines[1]?.title ?? "").slice(0, -1))).toBe(true);
  });

  it("is named for what the list is", () => {
    expect(EARLIER_ITEMS_AGENDA.label).toBe("지난 회의의 미완료 할 일");
  });
});
