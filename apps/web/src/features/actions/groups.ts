import type { ActionItemRead, Project, TeamName } from "./types";

/**
 * How the board across meetings is laid out (the user, 2026-10-06): everything
 * on one board, or one board per team, or one per project. A layout and not a
 * filter -- every item stays on the screen in all three.
 */
export type BoardView = "all" | "team" | "project";

export interface ItemGroup<T> {
  key: string;
  title: string;
  /** Said beside the title: a project's team, when the items are of several teams. */
  note: string | null;
  items: T[];
}

export const UNNAMED_TEAM = "이름을 불러오지 못한 팀";
export const UNNAMED_PROJECT = "이름을 불러오지 못한 프로젝트";
export const NO_PROJECT = "미분류";

type Row = Pick<ActionItemRead, "team_id" | "project_id">;

/** `items` split by `keyOf`, each group in the order its first item came. */
function split<T>(items: T[], keyOf: (item: T) => string): Map<string, T[]> {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = keyOf(item);
    const group = groups.get(key);
    if (group === undefined) groups.set(key, [item]);
    else group.push(item);
  }
  return groups;
}

/**
 * One group per team that has an item, in the order of `teams` (the server's:
 * by name). A team whose name did not arrive keeps its own group, after the
 * named ones, rather than being folded into another team's.
 */
export function byTeam<T extends Row>(
  items: T[],
  teams: TeamName[],
): ItemGroup<T>[] {
  const groups = split(items, (item) => item.team_id ?? "");
  const named = teams.flatMap((team) => {
    const own = groups.get(team.id);
    groups.delete(team.id);
    return own === undefined
      ? []
      : [{ key: team.id, title: team.name, note: null, items: own }];
  });
  const unnamed = [...groups].map(([key, own]) => ({
    key,
    title: UNNAMED_TEAM,
    note: null,
    items: own,
  }));
  return [...named, ...unnamed];
}

/**
 * One group per project that has an item, in the order of `projects` (the
 * server's: team by team, oldest first), then what no project took (미분류) --
 * the order the 요약 tab already groups in. Two teams can each have a project
 * of the same name, so a group says its team when the items are of several.
 */
export function byProject<T extends Row>(
  items: T[],
  projects: Project[],
  teams: TeamName[],
): ItemGroup<T>[] {
  const groups = split(items, (item) => item.project_id ?? "");
  const several = new Set(items.map((item) => item.team_id ?? "")).size > 1;
  const teamOf = (own: T[]) =>
    several
      ? (teams.find((team) => team.id === own[0]?.team_id)?.name ?? null)
      : null;

  const unsorted = groups.get("");
  groups.delete("");
  const named = projects.flatMap((project) => {
    const own = groups.get(project.id);
    groups.delete(project.id);
    return own === undefined
      ? []
      : [
          {
            key: project.id,
            title: project.name,
            note: teamOf(own),
            items: own,
          },
        ];
  });
  const unnamed = [...groups].map(([key, own]) => ({
    key,
    title: UNNAMED_PROJECT,
    note: teamOf(own),
    items: own,
  }));
  return [
    ...named,
    ...unnamed,
    ...(unsorted === undefined
      ? []
      : [{ key: "", title: NO_PROJECT, note: null, items: unsorted }]),
  ];
}
