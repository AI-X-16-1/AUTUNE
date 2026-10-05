import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ActionCompletionRate } from "./ActionCompletionRate";

// The rate is B's current counts (#605). Counts that are missing or stale are
// unknown, and the card must not read them as 0%.

const AS_OF = "2026-10-05T06:20:00Z";

afterEach(cleanup);

describe("ActionCompletionRate", () => {
  it("shows the rate, the overdue count and when the counts are from", () => {
    render(<ActionCompletionRate rate={0.625} overdue={2} asOf={AS_OF} />);

    expect(screen.getByText("63%")).toBeTruthy();
    expect(screen.getByText("기한 지난 항목 2건")).toBeTruthy();
    expect(screen.getByText(/기준$/)).toBeTruthy();
  });

  it("says the counts did not arrive rather than showing 0%", () => {
    render(<ActionCompletionRate rate={null} overdue={null} asOf={null} />);

    expect(screen.getByText("완료 현황을 아직 받지 못했습니다.")).toBeTruthy();
    expect(screen.queryByText(/%$/)).toBeNull();
  });

  it("says nothing is confirmed when the counts are current but empty", () => {
    render(<ActionCompletionRate rate={null} overdue={0} asOf={AS_OF} />);

    expect(screen.getByText("확정된 액션 아이템이 없습니다.")).toBeTruthy();
  });
});
