import { isOverdue, localToday } from "./dates";
import type { ActionItemRead, Project } from "./types";

/** One project's line on the team board's progress strip (2026-10-04). */
export interface ProjectProgress {
  project: Project;
  /** Confirmed items: what the team agreed to do. */
  total: number;
  done: number;
  overdue: number;
}

/**
 * Each project's confirmed items, how many are done and how many are late,
 * counted from the items the board already shows the reader -- nothing new is
 * fetched, so the strip can show no more than the board does. Items still
 * awaiting confirmation are left out: they are suggestions, not work. A
 * project with no confirmed item is skipped. Counts are per project, never per
 * person.
 */
export function projectProgress(
  items: Pick<ActionItemRead, "project_id" | "status" | "due_date">[],
  projects: Project[],
  today: string = localToday(),
): ProjectProgress[] {
  return projects
    .map((project) => {
      const mine = items.filter(
        (item) =>
          item.project_id === project.id &&
          (item.status ?? "needs_confirmation") !== "needs_confirmation",
      );
      return {
        project,
        total: mine.length,
        done: mine.filter((item) => item.status === "done").length,
        overdue: mine.filter((item) => isOverdue(item, today)).length,
      };
    })
    .filter((line) => line.total > 0);
}
