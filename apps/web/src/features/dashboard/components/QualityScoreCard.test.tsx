import { describe, expect, it } from "vitest";

import { mondayOf } from "./QualityScoreCard";

// #231: the quality bars group scores by the Korean week, as the weekly
// report does, not the UTC one.

describe("mondayOf", () => {
  it("puts Monday morning in Korea in that Monday's week", () => {
    // 2026-10-05 is a Monday. 08:30 KST is 23:30 UTC on Sunday the 4th.
    expect(mondayOf("2026-10-04T23:30:00Z")).toBe("2026-10-05");
  });

  it("keeps Sunday night in Korea in the week before", () => {
    // 23:30 KST on Sunday the 4th is 14:30 UTC that Sunday.
    expect(mondayOf("2026-10-04T14:30:00Z")).toBe("2026-09-28");
  });

  it("does not move a midweek score", () => {
    expect(mondayOf("2026-10-08T03:00:00Z")).toBe("2026-10-05");
  });
});
