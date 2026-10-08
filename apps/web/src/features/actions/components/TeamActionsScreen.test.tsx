import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TeamActionsScreen } from "./TeamActionsScreen";
import { UNNAMED_TEAM } from "../groups";
import type { JiraProjectIssues } from "../api";
import type { ActionItemRead, Project, TeamName } from "../types";

// The board across meetings laid out at once, team by team and project by
// project (the user, 2026-10-06). What matters: nothing leaves the screen when
// the layout changes, each item sits under its own team or project, and two
// teams' items are never merged because a name did not arrive.

const teams = vi.fn<() => Promise<TeamName[]>>();
const projects = vi.fn<() => Promise<Project[]>>();
const jira = vi.fn<() => Promise<JiraProjectIssues[]>>();
vi.mock("../api", () => ({
  listMyTeams: () => teams(),
  listMyProjects: () => projects(),
  bulkActionItems: vi.fn(),
  listJiraOpenIssues: () => jira(),
}));

// The drawer reads an item's detail on its own; here only whether it is open
// matters, and for which item.
vi.mock("./ActionDetailDrawer", () => ({
  ActionDetailDrawer: ({ item }: { item: ActionItemRead }) => (
    <aside aria-label="상세">{item.id}</aside>
  ),
}));

let items: ActionItemRead[] = [];
let error: string | null = null;
vi.mock("../hooks/useActionItems", () => ({
  useActionItems: () => ({
    items,
    settled: true,
    error,
    edit: vi.fn(),
    remove: vi.fn(),
    reload: vi.fn(),
  }),
}));

function item(id: string, extra: Partial<ActionItemRead>) {
  return {
    id,
    meeting_id: `mtg_${id}`,
    description: `항목 ${id}`,
    status: "todo",
    is_candidate: false,
    ...extra,
  } as ActionItemRead;
}

const TEAMS: TeamName[] = [
  { id: "team_d", name: "디자인" },
  { id: "team_p", name: "플랫폼" },
];
const PROJECTS: Project[] = [
  { id: "prj_web", name: "웹", aliases: [], jira_project_key: null },
];

const press = (name: string) =>
  fireEvent.click(screen.getByRole("button", { name }));
const group = (name: string) => screen.getByRole("region", { name });
const inGroup = (name: string) =>
  ["a", "b", "c"].filter(
    (id) => within(group(name)).queryByText(`항목 ${id}`) !== null,
  );
const onScreen = () =>
  ["a", "b", "c"].filter((id) => screen.queryByText(`항목 ${id}`) !== null);

beforeEach(() => {
  items = [
    item("a", { team_id: "team_p", project_id: "prj_web" }),
    item("b", { team_id: "team_d", project_id: null }),
    item("c", { team_id: "team_p", project_id: null }),
  ];
  teams.mockResolvedValue(TEAMS);
  projects.mockResolvedValue(PROJECTS);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  error = null;
});

async function open() {
  render(<TeamActionsScreen me={null} />);
  await waitFor(() => expect(teams).toHaveBeenCalled());
  await screen.findByRole("combobox", { name: "프로젝트로 거르기" });
}

describe("TeamActionsScreen, 보기", () => {
  it("opens on everything at once, with no team or project heading", async () => {
    await open();

    expect(
      screen
        .getByRole("button", { name: "한번에" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(onScreen()).toEqual(["a", "b", "c"]);
    expect(screen.queryByRole("region", { name: "플랫폼" })).toBeNull();
    expect(screen.queryByRole("region", { name: "미분류" })).toBeNull();
  });

  it("puts each team's items under that team's name, and keeps them all", async () => {
    await open();
    press("팀별");

    expect(inGroup("디자인")).toEqual(["b"]);
    expect(inGroup("플랫폼")).toEqual(["a", "c"]);
    expect(within(group("플랫폼")).getByText("2건")).toBeTruthy();
    expect(onScreen()).toEqual(["a", "b", "c"]);
  });

  it("puts each project's items under its name and the rest under 미분류", async () => {
    await open();
    press("프로젝트별");

    // Two teams can each have a project of one name: the team is said too.
    expect(inGroup("웹 · 플랫폼")).toEqual(["a"]);
    expect(within(group("웹 · 플랫폼")).getByText("플랫폼")).toBeTruthy();
    expect(inGroup("미분류")).toEqual(["b", "c"]);
    expect(onScreen()).toEqual(["a", "b", "c"]);
  });

  it("goes back to one board", async () => {
    await open();
    press("팀별");
    press("한번에");

    expect(screen.queryByRole("region", { name: "플랫폼" })).toBeNull();
    expect(onScreen()).toEqual(["a", "b", "c"]);
  });

  it("keeps two teams apart when their names could not be read", async () => {
    teams.mockRejectedValue(new Error("down"));
    render(<TeamActionsScreen me={null} />);
    await screen.findByRole("combobox", { name: "프로젝트로 거르기" });
    press("팀별");

    const unnamed = screen.getAllByRole("region", { name: UNNAMED_TEAM });
    expect(unnamed).toHaveLength(2);
    expect(
      unnamed
        .map((region) => within(region).getAllByText(/^항목 /).length)
        .sort(),
    ).toEqual([1, 2]);
  });

  it("groups what the tab and the project filter left", async () => {
    await open();
    fireEvent.change(
      screen.getByRole("combobox", { name: "프로젝트로 거르기" }),
      {
        target: { value: "prj_web" },
      },
    );
    press("팀별");

    expect(onScreen()).toEqual(["a"]);
    expect(inGroup("플랫폼")).toEqual(["a"]);
    expect(screen.queryByRole("region", { name: "디자인" })).toBeNull();
  });
});

describe("TeamActionsScreen, two teams' projects of one name", () => {
  const TWINS: Project[] = [
    {
      id: "prj_web",
      team_id: "team_p",
      name: "웹",
      aliases: [],
      jira_project_key: null,
    },
    {
      id: "prj_web_d",
      team_id: "team_d",
      name: "웹",
      aliases: [],
      jira_project_key: null,
    },
  ];
  const options = () =>
    within(screen.getByRole("combobox", { name: "프로젝트로 거르기" }))
      .getAllByRole("option")
      .map((option) => option.textContent);

  beforeEach(() => {
    items = [
      item("a", { team_id: "team_p", project_id: "prj_web" }),
      item("b", { team_id: "team_d", project_id: "prj_web_d" }),
      item("c", { team_id: "team_p", project_id: null }),
    ];
    projects.mockResolvedValue(TWINS);
  });

  it("says the team in the filter, and each choice is its own team's", async () => {
    await open();
    await waitFor(() =>
      expect(options()).toEqual(["전체", "웹 · 플랫폼", "웹 · 디자인", "미분류"]),
    );

    fireEvent.change(
      screen.getByRole("combobox", { name: "프로젝트로 거르기" }),
      { target: { value: "prj_web_d" } },
    );

    expect(onScreen()).toEqual(["b"]);
  });

  it("says the team on each line of the progress strip", async () => {
    await open();

    const strip = await screen.findByRole("list", { name: "프로젝트 진행" });
    await waitFor(() =>
      expect(
        within(strip)
          .getAllByRole("button")
          .map((button) => button.textContent),
      ).toEqual(["웹 · 플랫폼완료 0/1", "웹 · 디자인완료 0/1"]),
    );

    fireEvent.click(within(strip).getByRole("button", { name: /디자인/ }));

    expect(onScreen()).toEqual(["b"]);
  });

  it("says no team when the projects are all of one team", async () => {
    projects.mockResolvedValue([
      TWINS[0]!,
      { ...TWINS[1]!, team_id: "team_p", name: "앱" },
    ]);
    await open();

    await waitFor(() => expect(options()).toEqual(["전체", "웹", "앱", "미분류"]));
    const strip = await screen.findByRole("list", { name: "프로젝트 진행" });
    expect(
      within(strip)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["웹완료 0/1", "앱완료 0/1"]);
  });

  it("says no team when the teams' names could not be read", async () => {
    teams.mockRejectedValue(new Error("down"));
    render(<TeamActionsScreen me={null} />);
    await screen.findByRole("combobox", { name: "프로젝트로 거르기" });

    await waitFor(() => expect(options()).toEqual(["전체", "웹", "웹", "미분류"]));
  });
});

// A team pressed in the sidebar while the board is open (the user,
// 2026-10-08): the route passes it in as `teamId`. What matters: only that
// team's items are on the screen and counted, every team's come back, and
// nothing chosen for another team is left holding the board empty.
describe("TeamActionsScreen, one team's items", () => {
  const OF_TEAMS: Project[] = [
    { id: "prj_web", team_id: "team_p", name: "웹", aliases: [], jira_project_key: null },
    { id: "prj_brand", team_id: "team_d", name: "브랜드", aliases: [], jira_project_key: null },
  ];
  const filter = () =>
    screen.getByRole<HTMLSelectElement>("combobox", { name: "프로젝트로 거르기" });
  const options = () =>
    within(filter())
      .getAllByRole("option")
      .map((option) => option.textContent);
  const drawer = () => screen.queryByRole("complementary", { name: "상세" });

  async function show(teamId: string | null) {
    const onEveryTeam = vi.fn();
    const screenOf = (team: string | null) => (
      <TeamActionsScreen me={null} teamId={team} onEveryTeam={onEveryTeam} />
    );
    const view = render(screenOf(teamId));
    await waitFor(() => expect(teams).toHaveBeenCalled());
    await screen.findByRole("combobox", { name: "프로젝트로 거르기" });
    return { to: (team: string | null) => view.rerender(screenOf(team)), onEveryTeam };
  }

  it("shows every team's items until a team is passed in, then that team's alone", async () => {
    const { to } = await show(null);
    expect(onScreen()).toEqual(["a", "b", "c"]);
    expect(screen.queryByRole("status")).toBeNull();

    to("team_p");
    expect(onScreen()).toEqual(["a", "c"]);
    expect(screen.getByRole("status").textContent).toContain(
      "플랫폼의 액션 아이템만 보고 있습니다.",
    );

    to("team_d");
    expect(onScreen()).toEqual(["b"]);
    expect(screen.getByRole("status").textContent).toContain("디자인의 액션 아이템만");

    to(null);
    expect(onScreen()).toEqual(["a", "b", "c"]);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("counts the tabs of that team's items", async () => {
    const { to } = await show(null);
    expect(screen.getByRole("tab", { name: /전체/ }).textContent).toContain("3");

    to("team_d");

    expect(screen.getByRole("tab", { name: /전체/ }).textContent).toContain("1");
  });

  it("offers the way back to every team", async () => {
    const { onEveryTeam } = await show("team_p");

    press("전체 보기");

    expect(onEveryTeam).toHaveBeenCalledOnce();
  });

  it("says so when the team has no item, and names a team it cannot name", async () => {
    await show("team_gone");

    expect(onScreen()).toEqual([]);
    expect(screen.getByRole("status").textContent).toContain(UNNAMED_TEAM);
    expect(screen.getByText(/아직 액션 아이템이 없습니다/)).toBeTruthy();
  });

  it("says the list is an earlier one, not that it failed, when the team has none of it", async () => {
    // The last read failed and the list on hand is an earlier one. A team
    // with no item in it is an empty board, not a list that did not arrive.
    error = "failed";
    await show("team_gone");

    expect(screen.getByText(/이전 목록을 보여주고 있습니다/)).toBeTruthy();
    expect(screen.queryByText("액션 아이템을 불러오지 못했습니다.")).toBeNull();
  });

  it("lists that team's projects in the filter, and lets another team's go", async () => {
    projects.mockResolvedValue(OF_TEAMS);
    items = [
      item("a", { team_id: "team_p", project_id: "prj_web" }),
      item("b", { team_id: "team_d", project_id: "prj_brand" }),
      item("c", { team_id: "team_p", project_id: null }),
    ];
    const { to } = await show(null);
    await waitFor(() =>
      expect(options()).toEqual(["전체", "웹 · 플랫폼", "브랜드 · 디자인", "미분류"]),
    );
    fireEvent.change(filter(), { target: { value: "prj_web" } });
    expect(onScreen()).toEqual(["a"]);

    // Its own team pressed: the choice holds.
    to("team_p");
    expect(filter().value).toBe("prj_web");
    expect(onScreen()).toEqual(["a"]);

    // Another team: 웹 is not theirs, so the board is theirs and not empty.
    to("team_d");
    expect(options()).toEqual(["전체", "브랜드", "미분류"]);
    expect(filter().value).toBe("all");
    expect(onScreen()).toEqual(["b"]);

    // And it does not come back with every team.
    to(null);
    expect(filter().value).toBe("all");
    expect(onScreen()).toEqual(["a", "b", "c"]);
  });

  it("keeps 미분류 chosen across teams", async () => {
    const { to } = await show(null);
    fireEvent.change(filter(), { target: { value: "unsorted" } });
    expect(onScreen()).toEqual(["b", "c"]);

    to("team_p");

    expect(filter().value).toBe("unsorted");
    expect(onScreen()).toEqual(["c"]);
  });

  it("closes an open item that is not that team's, and does not reopen it", async () => {
    const { to } = await show(null);
    fireEvent.click(screen.getByText("항목 a"));
    expect(drawer()?.textContent).toBe("a");

    to("team_d");
    expect(drawer()).toBeNull();

    to(null);
    expect(drawer()).toBeNull();
  });

  it("leaves an open item of that team open", async () => {
    const { to } = await show(null);
    fireEvent.click(screen.getByText("항목 a"));

    to("team_p");

    expect(drawer()?.textContent).toBe("a");
  });

  it("lists that team's Jira project alone under the board", async () => {
    const of = (team_id: string, team_name: string): JiraProjectIssues => ({
      team_id,
      team_name,
      project_key: null,
      state: "ok",
      more: false,
      issues: [],
    });
    jira.mockResolvedValue([of("team_p", "플랫폼"), of("team_d", "디자인")]);
    const { to } = await show(null);
    press("Jira 열린 이슈 보기");
    const listed = () =>
      within(screen.getByRole("region", { name: "Jira 열린 이슈" }))
        .getAllByRole("heading", { level: 3 })
        .map((heading) => heading.textContent);
    await waitFor(() => expect(listed()).toEqual(["플랫폼", "디자인"]));

    to("team_d");

    expect(listed()).toEqual(["디자인"]);
  });

  it("lays 팀별 out with that team alone", async () => {
    const { to } = await show(null);
    press("팀별");

    to("team_p");

    expect(inGroup("플랫폼")).toEqual(["a", "c"]);
    expect(screen.queryByRole("region", { name: "디자인" })).toBeNull();
  });
});
