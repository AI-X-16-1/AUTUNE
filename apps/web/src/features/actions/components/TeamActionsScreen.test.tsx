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
import type { ActionItemRead, Project, TeamName } from "../types";

// The board across meetings laid out at once, team by team and project by
// project (the user, 2026-10-06). What matters: nothing leaves the screen when
// the layout changes, each item sits under its own team or project, and two
// teams' items are never merged because a name did not arrive.

const teams = vi.fn<() => Promise<TeamName[]>>();
const projects = vi.fn<() => Promise<Project[]>>();
vi.mock("../api", () => ({
  listMyTeams: () => teams(),
  listMyProjects: () => projects(),
  bulkActionItems: vi.fn(),
  listJiraOpenIssues: vi.fn(),
}));

let items: ActionItemRead[] = [];
vi.mock("../hooks/useActionItems", () => ({
  useActionItems: () => ({
    items,
    settled: true,
    error: null,
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
