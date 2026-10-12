import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SinceLastMeetingSection } from "./SinceLastMeetingSection";
import type { JiraProjectIssues, SinceLastMeeting } from "../api";
import type { ActionItemRead } from "../types";

// What moved on the board since each team's last meeting (the user,
// 2026-10-12). Read only when asked for; the rows are the board's own items.

const since = vi.fn<() => Promise<SinceLastMeeting[]>>();
const jira = vi.fn<() => Promise<JiraProjectIssues[]>>();
vi.mock("../api", () => ({
  listSinceLastMeeting: () => since(),
  listJiraOpenIssues: () => jira(),
}));

const item = (id: string, overrides: Partial<ActionItemRead> = {}): ActionItemRead =>
  ({
    id,
    meeting_id: "mtg_1",
    team_id: "team_1",
    description: `${id} 하기`,
    title: null,
    assignee_id: "user_kim",
    assignee_name: "김가나",
    assignee_label: null,
    due_date: null,
    status: "todo",
    needs_reassignment: false,
    ...overrides,
  }) as ActionItemRead;

const ITEMS = [
  item("act_done", { status: "done", description: "배포 점검표 쓰기" }),
  item("act_closed", {
    status: "done",
    description: "구 버전 문서 정리",
    assignee_id: null,
    assignee_name: null,
    assignee_label: "박외주",
  }),
  item("act_late", { description: "견적서 보내기", due_date: "2026-10-02" }),
  item("act_doing", {
    status: "in_progress",
    description: "검색 속도 재기",
    needs_reassignment: true,
  }),
  item("act_new", { description: "온보딩 문구 고치기", assignee_name: null }),
];

const ANSWER: SinceLastMeeting = {
  team_id: "team_1",
  team_name: "제품팀",
  state: "ok",
  meeting_id: "mtg_1",
  meeting_title: "주간 회의",
  since: "2026-10-08T05:00:00Z",
  finished: ["act_done"],
  closed: ["act_closed"],
  late: ["act_late"],
  started: ["act_doing"],
  created: ["act_new"],
  open: 4,
};

const PROJECT: JiraProjectIssues = {
  team_id: "team_1",
  team_name: "제품팀",
  project_key: "AUT",
  state: "ok",
  more: false,
  issues: ["AUT-1", "AUT-2", "AUT-3"].map((key) => ({
    key,
    summary: `${key} 이슈`,
    status: null,
    status_category: null,
    assignee: null,
    due_date: null,
    url: null,
    from_autune: false,
  })),
};

const open = () => fireEvent.click(screen.getByRole("button", { name: "지난 회의 이후 보기" }));
const part = (heading: string) => {
  const list = screen.getByRole("heading", { name: heading }).nextElementSibling;
  return within(list as HTMLElement);
};

afterEach(() => {
  cleanup();
  since.mockReset();
  jira.mockReset();
});

describe("SinceLastMeetingSection", () => {
  it("asks for nothing until it is opened", () => {
    render(<SinceLastMeetingSection items={ITEMS} onOpen={() => {}} />);

    expect(since).not.toHaveBeenCalled();
    expect(jira).not.toHaveBeenCalled();
  });

  it("draws each part from the board's own items, under the team and its last meeting", async () => {
    since.mockResolvedValue([ANSWER]);
    jira.mockResolvedValue([PROJECT]);
    render(<SinceLastMeetingSection items={ITEMS} onOpen={() => {}} />);

    open();

    expect(await screen.findByText(/^제품팀 · 지난 회의: 주간 회의 \(/)).toBeTruthy();
    expect(part("끝난 일 · 1건").getByRole("button", { name: "배포 점검표 쓰기" })).toBeTruthy();
    expect(
      part("끝내지 않고 닫힌 일 · 1건").getByRole("button", { name: "구 버전 문서 정리" }),
    ).toBeTruthy();
    expect(part("늦은 일 · 1건").getByRole("button", { name: "견적서 보내기" })).toBeTruthy();
    expect(part("시작한 일 · 1건").getByRole("button", { name: "검색 속도 재기" })).toBeTruthy();
    expect(part("새로 생긴 일 · 1건").getByRole("button", { name: "온보딩 문구 고치기" })).toBeTruthy();
    // The due date as the card writes it, and its gap named.
    const year = new Date().getFullYear() === 2026 ? "" : "2026년 ";
    expect(part("늦은 일 · 1건").getByText(`${year}10월 2일 금`)).toBeTruthy();
    expect(part("새로 생긴 일 · 1건").getByText("기한 없음")).toBeTruthy();
    expect(screen.getByText("열린 할 일 4건 · Jira 열린 이슈 3건")).toBeTruthy();
    expect(screen.getByText(/따로 저장하지 않습니다/)).toBeTruthy();
  });

  it("opens an item as its card does", async () => {
    since.mockResolvedValue([ANSWER]);
    jira.mockResolvedValue([]);
    const onOpen = vi.fn();
    render(<SinceLastMeetingSection items={ITEMS} onOpen={onOpen} />);

    open();
    fireEvent.click(await screen.findByRole("button", { name: "검색 속도 재기" }));

    expect(onOpen).toHaveBeenCalledWith("act_doing");
  });

  it("leaves out a part with nothing in it, and an id the board does not hold", async () => {
    since.mockResolvedValue([
      { ...ANSWER, closed: [], late: [], started: [], created: [], finished: ["act_done", "act_gone"] },
    ]);
    jira.mockResolvedValue([]);
    render(<SinceLastMeetingSection items={ITEMS} onOpen={() => {}} />);

    open();

    expect(await screen.findByRole("heading", { name: "끝난 일 · 1건" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /늦은 일/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: /끝내지 않고 닫힌 일/ })).toBeNull();
  });

  it("says so when nothing moved, and still says how many are open", async () => {
    since.mockResolvedValue([
      { ...ANSWER, finished: [], closed: [], late: [], started: [], created: [] },
    ]);
    jira.mockResolvedValue([]);
    render(<SinceLastMeetingSection items={ITEMS} onOpen={() => {}} />);

    open();

    expect(await screen.findByText("지난 회의 이후로 바뀐 할 일이 없습니다.")).toBeTruthy();
    expect(screen.getByText("열린 할 일 4건")).toBeTruthy();
  });

  it("tells a team with no analysed meeting that there is nothing to count from", async () => {
    since.mockResolvedValue([
      {
        ...ANSWER,
        state: "no_meeting",
        meeting_id: null,
        meeting_title: null,
        since: null,
        finished: [],
        closed: [],
        late: [],
        started: [],
        created: [],
        open: 0,
      },
    ]);
    jira.mockResolvedValue([PROJECT]);
    render(<SinceLastMeetingSection items={ITEMS} onOpen={() => {}} />);

    open();

    expect(await screen.findByText(/분석을 마친 회의가 아직 없습니다/)).toBeTruthy();
    expect(screen.queryByText(/열린 할 일/)).toBeNull();
  });

  it.each([
    ["has more than were read", { ...PROJECT, more: true }, "열린 할 일 4건 · Jira 열린 이슈 3건 이상"],
    ["has no project chosen", { ...PROJECT, state: "no_project", issues: [] }, "열린 할 일 4건"],
    ["did not answer", { ...PROJECT, state: "unavailable", issues: [] }, "열린 할 일 4건"],
  ] as const)("counts Jira only when Jira said a count: %s", async (_why, project, line) => {
    since.mockResolvedValue([ANSWER]);
    jira.mockResolvedValue([project as JiraProjectIssues]);
    render(<SinceLastMeetingSection items={ITEMS} onOpen={() => {}} />);

    open();

    expect(await screen.findByText(line)).toBeTruthy();
  });

  it("still shows the board's parts when the Jira read fails", async () => {
    since.mockResolvedValue([ANSWER]);
    jira.mockRejectedValue(new Error("down"));
    render(<SinceLastMeetingSection items={ITEMS} onOpen={() => {}} />);

    open();

    expect(await screen.findByRole("heading", { name: "끝난 일 · 1건" })).toBeTruthy();
    expect(screen.getByText("열린 할 일 4건")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says it could not read, and reads again when asked", async () => {
    since.mockRejectedValueOnce(new Error("down"));
    jira.mockResolvedValue([]);
    render(<SinceLastMeetingSection items={ITEMS} onOpen={() => {}} />);

    open();

    expect((await screen.findByRole("alert")).textContent).toMatch(/불러오지 못했습니다/);
    since.mockResolvedValue([ANSWER]);
    fireEvent.click(screen.getByRole("button", { name: "다시 불러오기" }));
    expect(await screen.findByRole("heading", { name: "끝난 일 · 1건" })).toBeTruthy();
  });

  it("shows one team's answer alone when the board shows one team", async () => {
    const OTHER: SinceLastMeeting = {
      ...ANSWER,
      team_id: "team_2",
      team_name: "디자인팀",
      meeting_title: "디자인 리뷰",
    };
    since.mockResolvedValue([ANSWER, OTHER]);
    jira.mockResolvedValue([]);
    const view = render(<SinceLastMeetingSection items={ITEMS} teamId="team_2" onOpen={() => {}} />);

    open();

    expect(await screen.findByText(/^디자인팀 · 지난 회의: 디자인 리뷰/)).toBeTruthy();
    expect(screen.queryByText(/^제품팀 · 지난 회의/)).toBeNull();

    // Every team again: nothing is read anew.
    view.rerender(<SinceLastMeetingSection items={ITEMS} teamId={null} onOpen={() => {}} />);
    expect(screen.getByText(/^제품팀 · 지난 회의/)).toBeTruthy();
    expect(since).toHaveBeenCalledTimes(1);
  });

  it("drops an answer that arrives after it was closed", async () => {
    let answer: (value: SinceLastMeeting[]) => void = () => {};
    since.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    jira.mockResolvedValue([]);
    render(<SinceLastMeetingSection items={ITEMS} onOpen={() => {}} />);

    open();
    fireEvent.click(screen.getByRole("button", { name: "접기" }));
    await act(async () => answer([ANSWER]));

    expect(screen.getByRole("button", { name: "지난 회의 이후 보기" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /끝난 일/ })).toBeNull();
  });

  it("names nobody: a row is an item's title and its due date, whoever holds it", async () => {
    since.mockResolvedValue([ANSWER]);
    jira.mockResolvedValue([]);
    const view = render(<SinceLastMeetingSection items={ITEMS} onOpen={() => {}} />);

    open();
    await screen.findByRole("heading", { name: "끝난 일 · 1건" });

    // A member's name, a name typed for somebody outside the team, the mark
    // of an item whose owner left, and the word for an item nobody holds:
    // each would say who, under a heading that says what happened since.
    const drawn = view.container.textContent ?? "";
    for (const owner of ["김가나", "박외주", "user_kim", "재배정", "담당"]) {
      expect(drawn).not.toContain(owner);
    }
  });
});
