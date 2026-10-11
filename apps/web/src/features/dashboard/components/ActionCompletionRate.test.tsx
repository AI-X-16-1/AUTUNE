import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ActionCompletionRate } from "./ActionCompletionRate";

// The rate is B's current counts (#605). Counts that are missing or stale are
// unknown, and the card must not read them as 0%. A total from fewer than
// three meetings is withheld by the server (#800 review).

const AS_OF = "2026-10-05T06:20:00Z";

afterEach(cleanup);

describe("ActionCompletionRate", () => {
  it("shows the rate, the overdue count and when the counts are from", () => {
    render(<ActionCompletionRate rate={0.625} meetings={3} overdue={2} asOf={AS_OF} />);

    expect(screen.getByText("63%")).toBeTruthy();
    expect(screen.getByText("기한 지난 항목 2건")).toBeTruthy();
    expect(screen.getByText(/보관 중인 회의 전체/)).toBeTruthy();
    expect(screen.getByText(/완료율은 최근 4주 회의 · .* 기준$/)).toBeTruthy();
  });

  it("says the counts did not arrive rather than showing 0%", () => {
    render(<ActionCompletionRate rate={null} meetings={null} overdue={null} asOf={null} />);

    expect(screen.getByText("완료 현황을 아직 받지 못했습니다.")).toBeTruthy();
    expect(screen.queryByText(/%$/)).toBeNull();
  });

  it("says nothing is confirmed when the counts are current but empty", () => {
    render(<ActionCompletionRate rate={null} meetings={0} overdue={0} asOf={AS_OF} />);

    expect(screen.getByText("최근 4주 회의에서 확정된 할 일이 없습니다.")).toBeTruthy();
  });

  it("says why there is no rate when the window holds fewer than three meetings", () => {
    render(<ActionCompletionRate rate={null} meetings={2} overdue={null} asOf={AS_OF} />);

    expect(
      screen.getByText("확정 항목이 있는 최근 4주 회의가 3건 미만이라 완료율을 표시하지 않습니다."),
    ).toBeTruthy();
    expect(screen.queryByText(/기한 지난 항목/)).toBeNull();
  });

  it("does not blame the floor when the window holds three meetings or more", () => {
    render(<ActionCompletionRate rate={null} meetings={4} overdue={0} asOf={AS_OF} />);

    expect(screen.getByText("최근 4주 회의에서 확정된 할 일이 없습니다.")).toBeTruthy();
    expect(screen.queryByText(/3건 미만/)).toBeNull();
  });
});
