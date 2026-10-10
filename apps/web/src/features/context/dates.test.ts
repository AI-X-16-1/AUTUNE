import { describe, expect, it } from "vitest";

import { kstDay } from "./dates";

describe("kstDay", () => {
  it("is the next day for a meeting before 09:00 KST", () => {
    // 2026-10-08T23:30Z is 08:30 on the 9th in Seoul.
    expect(kstDay("2026-10-08T23:30:00+00:00")).toBe("2026-10-09");
    expect(kstDay("2026-10-08T15:00:00Z")).toBe("2026-10-09");
  });

  it("is the same day once it is 09:00 KST or later", () => {
    expect(kstDay("2026-10-09T00:00:00+00:00")).toBe("2026-10-09");
    expect(kstDay("2026-10-09T14:59:59Z")).toBe("2026-10-09");
  });

  it("reads an offset other than UTC", () => {
    expect(kstDay("2026-12-31T23:30:00+09:00")).toBe("2026-12-31");
    expect(kstDay("2026-12-31T15:00:00+00:00")).toBe("2027-01-01");
  });
});
