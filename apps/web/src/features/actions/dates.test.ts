import { describe, expect, it } from "vitest";

import { isSensibleDue, quickDues, shownDue, writtenDay } from "./dates";

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

describe("quickDues", () => {
  const days = (now: Date) => quickDues(now).map((pick) => [pick.label, pick.iso]);

  it("offers tomorrow, this week's Friday and next week's Monday", () => {
    // Tue 2026-10-13.
    expect(days(new Date(2026, 9, 13))).toEqual([
      ["내일", "2026-10-14"],
      ["이번 주 금", "2026-10-16"],
      ["다음 주 월", "2026-10-19"],
    ]);
  });

  it("is today on a Friday, never a day that has passed on the weekend", () => {
    // Fri 2026-10-16: Friday is today, tomorrow is Saturday.
    expect(days(new Date(2026, 9, 16))[1]).toEqual(["이번 주 금", "2026-10-16"]);
    // Sat 2026-10-17 and Sun 2026-10-18 both look to next week's Friday.
    expect(days(new Date(2026, 9, 17))[1]).toEqual(["이번 주 금", "2026-10-23"]);
    expect(days(new Date(2026, 9, 18))[1]).toEqual(["이번 주 금", "2026-10-23"]);
  });

  it("does not show one day twice, and crosses a month end", () => {
    // Sun 2026-10-18: tomorrow is next Monday -- shown once, as 내일.
    expect(days(new Date(2026, 9, 18)).map(([label]) => label)).toEqual(["내일", "이번 주 금"]);
    // Thu 2026-10-29 -> tomorrow is Fri 10-30, Monday is 11-02.
    expect(days(new Date(2026, 9, 29))).toEqual([
      ["내일", "2026-10-30"],
      ["다음 주 월", "2026-11-02"],
    ]);
  });
});

describe("isSensibleDue", () => {
  it("takes a real day in a year from 2000 to 2100", () => {
    expect(isSensibleDue("2026-10-14")).toBe(true);
    expect(isSensibleDue("2000-01-01")).toBe(true);
    expect(isSensibleDue("2100-12-31")).toBe(true);
    expect(isSensibleDue("2028-02-29")).toBe(true);
  });

  it("refuses the years a half-typed year passes through, and days that do not exist", () => {
    for (const bad of ["0002-10-14", "0020-10-14", "0202-10-14", "1999-12-31", "2101-01-01"]) {
      expect(isSensibleDue(bad)).toBe(false);
    }
    for (const bad of ["2026-02-30", "2027-02-29", "2026-13-01", "2026-10-00"]) {
      expect(isSensibleDue(bad)).toBe(false);
    }
    for (const bad of ["", "2026-10-1", "tomorrow"]) {
      expect(isSensibleDue(bad)).toBe(false);
    }
  });
});
