import { afterEach, describe, expect, it, vi } from "vitest";

import { shownStatement } from "./statement";
import { shortTitle } from "./title";

// A decision's deadline on these screens reads like the page's other dates
// (the user, 2026-10-09). The stored sentence is not what changes.

afterEach(() => {
  vi.useRealTimers();
});

describe("shownStatement", () => {
  it("writes the deadline that closes the sentence as the page writes a day", () => {
    expect(shownStatement("배포는 다음 주로 미룹니다 (기한 2026-10-13)", 2026)).toBe(
      "배포는 다음 주로 미룹니다 (기한 10월 13일 화)",
    );
    expect(shownStatement("검색 정렬은 인기순으로 (담당 박지영, 기한 2026-10-02)", 2026)).toBe(
      "검색 정렬은 인기순으로 (담당 박지영, 기한 10월 2일 금)",
    );
  });

  it("says the year when it is not the one the reader takes for granted", () => {
    const next = "예산안은 새해에 확정합니다 (기한 2027-01-04)";

    expect(shownStatement(next, 2026)).toBe("예산안은 새해에 확정합니다 (기한 2027년 1월 4일 월)");
    expect(shownStatement(next, 2027)).toBe("예산안은 새해에 확정합니다 (기한 1월 4일 월)");
    // A page with no date line says no year for the reader to borrow.
    expect(shownStatement(next, null)).toBe("예산안은 새해에 확정합니다 (기한 2027년 1월 4일 월)");
  });

  it("takes this year for granted on a row that has no date line", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 9, 9, 12));

    expect(shownStatement("공지는 금요일에 올립니다 (기한 2026-10-16)")).toBe(
      "공지는 금요일에 올립니다 (기한 10월 16일 금)",
    );
    expect(shownStatement("예산안은 새해에 확정합니다 (기한 2027-01-04)")).toBe(
      "예산안은 새해에 확정합니다 (기한 2027년 1월 4일 월)",
    );
  });

  it("leaves a date somebody said in the sentence as it was said", () => {
    const said = "2026-10-13에 배포하기로 했습니다";
    expect(shownStatement(said, 2026)).toBe(said);
    expect(shownStatement("출시는 2026-10-13로 옮깁니다 (기한 2026-10-16)", 2026)).toBe(
      "출시는 2026-10-13로 옮깁니다 (기한 10월 16일 금)",
    );
    const inside = "일정은 (기한 2026-10-13) 다시 봅니다";
    expect(shownStatement(inside, 2026)).toBe(inside);
  });

  it("leaves everything else as it came", () => {
    for (const sentence of [
      "회의실은 예약제로 한다",
      "시안을 공유합니다 (담당 박지영)",
      // A deadline the server could not put a day to is its own words.
      "시안을 공유합니다 (기한 다음 주 금요일)",
      "시안을 공유합니다 (기한 2026-10-13",
      "",
    ]) {
      expect(shownStatement(sentence, 2026)).toBe(sentence);
    }
  });

  it("is cut for a row's top line with the day whole or not at all", () => {
    // The day fits: month and day stay together, the weekday is what is cut.
    expect(shortTitle(shownStatement("배포 미룹니다 (기한 2026-10-13)", 2026))).toEqual({
      shown: "배포 미룹니다 (기한 10월 13일…",
      cut: true,
    });
    // One character more and it does not: the line ends where the ISO one did.
    const longer = "배포는 미룹니다 (기한 2026-10-13)";
    expect(shortTitle(shownStatement(longer, 2026))).toEqual(shortTitle(longer));
    expect(shortTitle(longer).shown).toBe("배포는 미룹니다 (기한…");
  });
});
