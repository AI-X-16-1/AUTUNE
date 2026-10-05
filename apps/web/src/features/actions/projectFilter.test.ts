import { describe, expect, it } from "vitest";

import { ALL_PROJECTS, UNSORTED, inProject } from "./projectFilter";

// The board's project filter (the user, 2026-10-04).

const ROWS = [
  { id: "a", project_id: "prj_a" },
  { id: "b", project_id: "prj_b" },
  { id: "c", project_id: null },
  { id: "d" },
];

describe("inProject", () => {
  it("keeps everything for 전체", () => {
    expect(inProject(ROWS, ALL_PROJECTS).map((r) => r.id)).toEqual([
      "a",
      "b",
      "c",
      "d",
    ]);
  });

  it("keeps one project's rows", () => {
    expect(inProject(ROWS, "prj_b").map((r) => r.id)).toEqual(["b"]);
  });

  it("keeps the rows no project took for 미분류", () => {
    expect(inProject(ROWS, UNSORTED).map((r) => r.id)).toEqual(["c", "d"]);
  });
});
