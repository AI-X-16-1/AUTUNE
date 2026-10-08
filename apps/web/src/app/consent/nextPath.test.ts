import { describe, expect, it } from "vitest";

import { nextPath } from "./nextPath";

const HERE = "https://autune.example";

describe("where the consent page goes next", () => {
  it("follows a path on this site, with its query", () => {
    expect(nextPath("/actions?view=mine", HERE)).toBe("/actions?view=mine");
    expect(nextPath("/meetings/mtg_1/review#decisions", HERE)).toBe(
      "/meetings/mtg_1/review#decisions",
    );
  });

  it("goes home when nothing was asked", () => {
    expect(nextPath(null, HERE)).toBe("/");
    expect(nextPath("", HERE)).toBe("/");
  });

  it.each([
    ["another site", "https://example.com/x"],
    ["a scheme-relative address", "//example.com/x"],
    ["a backslash the browser reads as a slash", "/\\example.com/x"],
    ["two backslashes", "\\\\example.com/x"],
    ["a tab the browser drops", "/\t/example.com/x"],
    ["a newline the browser drops", "/\n/example.com/x"],
    ["a script address", "javascript:alert(1)"],
    ["a path with no leading slash", "actions"],
  ])("goes home for %s", (_what, next) => {
    expect(nextPath(next, HERE)).toBe("/");
  });

  it("never comes back to the consent page", () => {
    expect(nextPath("/consent", HERE)).toBe("/");
    expect(nextPath("/consent?next=/actions", HERE)).toBe("/");
    expect(nextPath("/consent/again", HERE)).toBe("/");
    expect(nextPath("/consents-report", HERE)).toBe("/consents-report");
  });

  it("follows the path as resolved, not as written", () => {
    expect(nextPath("/a/../actions", HERE)).toBe("/actions");
  });
});
