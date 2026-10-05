import type { ActionItemRead } from "./types";

/** What the board's project filter can be set to: everything, none, or one. */
export type ProjectChoice = "all" | "unsorted" | string;

export const ALL_PROJECTS: ProjectChoice = "all";
export const UNSORTED: ProjectChoice = "unsorted";

/** The items a project choice keeps (2026-10-04). */
export function inProject<T extends Pick<ActionItemRead, "project_id">>(
  items: T[],
  choice: ProjectChoice,
): T[] {
  if (choice === ALL_PROJECTS) return items;
  if (choice === UNSORTED) return items.filter((item) => !item.project_id);
  return items.filter((item) => item.project_id === choice);
}
