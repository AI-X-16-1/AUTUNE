import { describe, expect, it } from "vitest";

import { shownDue, writtenDay } from "./dates";

describe("writtenDay", () => {
  it("writes a day as month, day and weekday", () => {
    expect(writtenDay("2026-10-13", 2026)).toBe("10월 13일 화");
    expect(writtenDay("2026-10-02", 2026)).toBe("10월 2일 금");
    // No time is in a due date, so the first and last hours of a day are its own.
    expect(writtenDay("2026-01-01", 2026)).toBe("1월 1일 목");
    expect(writtenDay("2026-12-31", 2026)).toBe("12월 31일 목");
  });

  it("writes the year when it is another one, or when the page says none", () => {
    expect(writtenDay("2027-01-05", 2026)).toBe("2027년 1월 5일 화");
    expect(writtenDay("2026-10-13", null)).toBe("2026년 10월 13일 화");
  });

  it("is null for what is not a day as the server writes one", () => {
    for (const other of ["", "다음 주 금요일", "2026-10-13T00:00:00Z", "2026/10/13", "10-13"]) {
      expect(writtenDay(other, 2026)).toBeNull();
    }
  });
});

describe("shownDue", () => {
  it("leaves out this year and no other, and shows what it cannot read as it came", () => {
    const year = new Date().getFullYear();

    expect(shownDue(`${year}-03-02`)).toBe(writtenDay(`${year}-03-02`, year));
    expect(shownDue(`${year}-03-02`)).not.toContain("년");
    expect(shownDue(`${year + 1}-03-02`)).toContain(`${year + 1}년 3월 2일`);
    expect(shownDue("다음 주")).toBe("다음 주");
  });
});
