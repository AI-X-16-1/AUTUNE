import { describe, expect, it } from "vitest";

import { staleLabel } from "./stale";

// An open item carried through three or more meetings reads as stuck
// (the user, 2026-10-04).

describe("staleLabel", () => {
  it("marks an open item carried through three meetings", () => {
    expect(staleLabel({ status: "todo", carried_meetings: 3 })).toBe(
      "3회 넘어감",
    );
    expect(staleLabel({ status: "in_progress", carried_meetings: 5 })).toBe(
      "5회 넘어감",
    );
  });

  it("says nothing before three, or for a finished or draft item", () => {
    expect(staleLabel({ status: "todo", carried_meetings: 2 })).toBeNull();
    expect(staleLabel({ status: "done", carried_meetings: 9 })).toBeNull();
    expect(
      staleLabel({ status: "needs_confirmation", carried_meetings: 9 }),
    ).toBeNull();
    expect(staleLabel({ status: "todo" })).toBeNull();
  });
});
