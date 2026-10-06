import { describe, expect, it } from "vitest";

import {
  NO_PROJECT,
  UNNAMED_PROJECT,
  UNNAMED_TEAM,
  byProject,
  byTeam,
} from "./groups";
import type { Project, TeamName } from "./types";

// The board across meetings, team by team and project by project (the user,
// 2026-10-06). A layout: every item is in exactly one group.

const TEAMS: TeamName[] = [
  { id: "team_d", name: "디자인" },
  { id: "team_p", name: "플랫폼" },
  { id: "team_s", name: "영업" },
];

const project = (id: string, name: string): Project => ({
  id,
  name,
  aliases: [],
  jira_project_key: null,
});
const PROJECTS = [
  project("prj_app", "앱"),
  project("prj_web", "웹"),
  project("prj_app2", "앱"),
];

type Row = { id: string; team_id?: string | null; project_id?: string | null };

const ROWS: Row[] = [
  { id: "a", team_id: "team_p", project_id: "prj_web" },
  { id: "b", team_id: "team_d", project_id: "prj_app2" },
  { id: "c", team_id: "team_p", project_id: null },
  { id: "d", team_id: "team_p", project_id: "prj_app" },
  { id: "e", team_id: "team_d" },
  { id: "f", team_id: "team_p", project_id: "prj_web" },
];

const shape = (
  groups: { title: string; note: string | null; items: { id: string }[] }[],
) =>
  groups.map((group) => [
    group.title,
    group.note,
    group.items.map((item) => item.id).join(""),
  ]);

describe("byTeam", () => {
  it("heads each team's items with its name, in the teams' order", () => {
    expect(shape(byTeam(ROWS, TEAMS))).toEqual([
      ["디자인", null, "be"],
      ["플랫폼", null, "acdf"],
    ]);
  });

  it("keeps every item, each in one group", () => {
    const grouped = byTeam(ROWS, TEAMS).flatMap((group) =>
      group.items.map((item) => item.id),
    );
    expect(grouped.sort()).toEqual(["a", "b", "c", "d", "e", "f"]);
  });

  it("keeps a team whose name did not arrive apart, after the named ones", () => {
    const rows: Row[] = [...ROWS, { id: "g", team_id: "team_x" }, { id: "h" }];
    expect(shape(byTeam(rows, [TEAMS[1]!]))).toEqual([
      ["플랫폼", null, "acdf"],
      [UNNAMED_TEAM, null, "be"],
      [UNNAMED_TEAM, null, "g"],
      [UNNAMED_TEAM, null, "h"],
    ]);
  });

  it("is empty for no items", () => {
    expect(byTeam([], TEAMS)).toEqual([]);
  });
});

describe("byProject", () => {
  it("groups in the projects' order, 미분류 last, and names the team of each", () => {
    expect(shape(byProject(ROWS, PROJECTS, TEAMS))).toEqual([
      ["앱", "플랫폼", "d"],
      ["웹", "플랫폼", "af"],
      ["앱", "디자인", "b"],
      [NO_PROJECT, null, "ce"],
    ]);
  });

  it("does not name the team when every item is of one team", () => {
    const one = ROWS.filter((row) => row.team_id === "team_p");
    expect(shape(byProject(one, PROJECTS, TEAMS))).toEqual([
      ["앱", null, "d"],
      ["웹", null, "af"],
      [NO_PROJECT, null, "c"],
    ]);
  });

  it("keeps a project whose name did not arrive apart from 미분류", () => {
    expect(shape(byProject(ROWS, [PROJECTS[1]!], TEAMS))).toEqual([
      ["웹", "플랫폼", "af"],
      [UNNAMED_PROJECT, "디자인", "b"],
      [UNNAMED_PROJECT, "플랫폼", "d"],
      [NO_PROJECT, null, "ce"],
    ]);
  });

  it("has no 미분류 group when every item has a project", () => {
    const sorted = ROWS.filter((row) => row.project_id);
    expect(
      byProject(sorted, PROJECTS, TEAMS).map((group) => group.title),
    ).toEqual(["앱", "웹", "앱"]);
  });
});
