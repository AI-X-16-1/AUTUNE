import { describe, expect, it } from "vitest";

import { isPixelPath } from "./metaPixel";

describe("isPixelPath", () => {
  it("allows the screens before sign-in", () => {
    expect(isPixelPath("/login")).toBe(true);
    expect(isPixelPath("/consent")).toBe(true);
    expect(isPixelPath("/legal")).toBe(true);
  });

  // `/invite` carries the invitation token after `#`, and the pixel sends the
  // whole address; inside the app an address names a team or a meeting.
  it("refuses the invitation link and every screen of the app", () => {
    expect(isPixelPath("/invite")).toBe(false);
    expect(isPixelPath("/")).toBe(false);
    expect(isPixelPath("/meetings/mtg_1")).toBe(false);
    expect(isPixelPath("/loginx")).toBe(false);
    expect(isPixelPath(null)).toBe(false);
  });
});
