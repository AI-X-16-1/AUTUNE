import { describe, expect, it } from "vitest";

import { projectProgress } from "./projectProgress";
import type { ActionItemRead, Project } from "./types";

// Per-project progress on the team board (the user, 2026-10-04).

const A = { id: "prj_a", name: "알파", aliases: [] } as unknown as Project;
const B = { id: "prj_b", name: "베타", aliases: [] } as unknown as Project;
type Row = Pick<ActionItemRead, "project_id" | "status" | "due_date">;
const row = (over: Partial<Row>): Row => ({
  project_id: "prj_a",
  status: "todo",
  due_date: null,
  ...over,
});

describe("projectProgress", () => {
  it("counts confirmed, done and late items per project", () => {
    const lines = projectProgress(
      [
        row({ status: "done", due_date: "2026-09-01" }),
        row({ status: "in_progress", due_date: "2026-10-01" }),
        row({ status: "todo", due_date: "2026-10-09" }),
        row({ status: "needs_confirmation" }),
        row({ project_id: null, status: "todo" }),
      ],
      [A, B],
      "2026-10-04",
    );
    expect(lines).toEqual([{ project: A, total: 3, done: 1, overdue: 1 }]);
  });

  it("leaves out projects with only unconfirmed items", () => {
    expect(
      projectProgress(
        [row({ project_id: "prj_b", status: "needs_confirmation" })],
        [A, B],
      ),
    ).toEqual([]);
  });
});
