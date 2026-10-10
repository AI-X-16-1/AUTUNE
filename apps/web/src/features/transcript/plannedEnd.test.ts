import { afterEach, describe, expect, it } from "vitest";

import { forgetPlannedEnd, nextInstantAt, plannedEndOf, rememberPlannedEnd } from "./plannedEnd";

// The planned end of a live meeting, kept in the tab (#1147). What is pinned:
// which instant a typed clock time means, and that the tab's store is the only
// place it goes.

afterEach(() => window.sessionStorage.clear());

/** A local wall-clock time on a fixed day, whatever zone the test runs in. */
const at = (hours: number, minutes: number) => new Date(2026, 9, 9, hours, minutes, 0, 0);

describe("nextInstantAt", () => {
  it("is later today when the clock has not reached the time yet", () => {
    expect(nextInstantAt("15:30", at(14, 0))).toBe(at(15, 30).toISOString());
  });

  it("is tomorrow when that time has already passed today: 23:50 to end at 00:30", () => {
    const tomorrow = new Date(2026, 9, 10, 0, 30, 0, 0);

    expect(nextInstantAt("00:30", at(23, 50))).toBe(tomorrow.toISOString());
  });

  it("is tomorrow, not now, for the minute the clock is reading", () => {
    const tomorrow = new Date(2026, 9, 10, 14, 0, 0, 0);

    expect(nextInstantAt("14:00", at(14, 0))).toBe(tomorrow.toISOString());
  });

  it.each(["", "soon", "25:00", "12:60", "9:05", "12:30:00"])(
    "is nothing for %j, which is not a clock time",
    (typed) => {
      expect(nextInstantAt(typed, at(14, 0))).toBeNull();
    },
  );
});

describe("the tab's planned end", () => {
  it("is read back for the meeting it was kept for, and for no other", () => {
    rememberPlannedEnd("mtg_1", "2026-10-09T06:30:00.000Z");

    expect(plannedEndOf("mtg_1")).toBe("2026-10-09T06:30:00.000Z");
    expect(plannedEndOf("mtg_2")).toBeNull();
  });

  it("is in the tab's own store and not the browser's lasting one", () => {
    rememberPlannedEnd("mtg_1", "2026-10-09T06:30:00.000Z");

    expect(window.sessionStorage.length).toBe(1);
    expect(window.localStorage.length).toBe(0);
  });

  it("is gone once forgotten", () => {
    rememberPlannedEnd("mtg_1", "2026-10-09T06:30:00.000Z");
    forgetPlannedEnd("mtg_1");

    expect(plannedEndOf("mtg_1")).toBeNull();
    expect(window.sessionStorage.length).toBe(0);
  });

  it("reads something that is not an instant as no planned end", () => {
    window.sessionStorage.setItem("autune.plannedEnd.mtg_1", "not a time");

    expect(plannedEndOf("mtg_1")).toBeNull();
  });
});
