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
    expect(nextPath("/./actions", HERE)).toBe("/actions");
  });

  it("keeps two slashes that are inside a path", () => {
    expect(nextPath("/a//b", HERE)).toBe("/a//b");
  });

  // Each is this origin as written, and resolves to a path that begins with two
  // slashes: followed as a string, that is another site (reviews of #1031).
  it.each([
    ["a dot segment before two slashes", "/.//example.com/x"],
    ["two dots before two slashes", "/..//example.com/x"],
    ["a segment taken back before two slashes", "/a/..//example.com/x"],
    ["an encoded dot", "/%2e//example.com"],
    ["a backslash after the dot", "/.\\/example.com"],
    ["a tab between the dot and the slashes", "/.\t//example.com"],
    ["three slashes after the dot", "/.///example.com"],
    ["a host with a query", "/.//example.com?x=1"],
    ["a host behind an empty user", "/.//@example.com"],
  ])("goes home for %s", (_what, next) => {
    expect(nextPath(next, HERE)).toBe("/");
  });

  it.each([
    ["two slashes and nothing", "/.//"],
    ["two slashes and a query", "/.//?example.com"],
    ["two slashes and an empty port", "/.//:example.com"],
  ])("goes home for an answer no address can be made of: %s", (_what, next) => {
    expect(nextPath(next, HERE)).toBe("/");
  });

  it("goes home when the answer names this site as a host", () => {
    // "//autune.example/consent" is this origin, and its path is this page.
    expect(nextPath("/.//autune.example/consent", HERE)).toBe("/");
    expect(nextPath("/.//autune.example/actions", HERE)).toBe("/");
  });

  it("answers only with what this origin reads back as itself", () => {
    const pieces = ["/", "\\", ".", "..", "%2e", "%2f", "%5c", "\t", ";", "?", "#", "@", ":", "a"];
    const hosts = ["example.com/x", "autune.example/consent", "consent"];
    let tried = 0;
    for (const a of pieces)
      for (const b of pieces)
        for (const c of pieces)
          for (const host of hosts) {
            const answer = nextPath("/" + a + b + c + host, HERE);
            const read = new URL(answer, HERE);
            expect(read.origin).toBe(HERE);
            expect(read.pathname + read.search + read.hash).toBe(answer);
            expect(read.pathname === "/consent" || read.pathname.startsWith("/consent/")).toBe(
              false,
            );
            tried += 1;
          }
    expect(tried).toBe(14 * 14 * 14 * 3);
  });
});
