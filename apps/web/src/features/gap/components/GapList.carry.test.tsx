import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GapList } from "./GapList";
import { DEMO_EXPLANATIONS, DEMO_REPORT } from "../fixtures/report-demo";
import type { GapExplanations } from "../types";

// "다음 회의 어젠다로" (#824): a mark on the gap, taken back by the same button.
// "담당자 지정해 질문" is still not wired.

const gaps = (DEMO_REPORT.gaps ?? []).slice(0, 1);
const gapId = gaps[0]!.id;

const explained = (carried: boolean): GapExplanations => ({
  ...DEMO_EXPLANATIONS,
  gaps: DEMO_EXPLANATIONS.gaps.map((e) => (e.gap_id === gapId ? { ...e, carried } : e)),
});

const card = () => screen.getAllByRole("article")[0]!;

afterEach(cleanup);

describe("GapList — 다음 회의 어젠다로", () => {
  it("sends the gap on when pressed", () => {
    const onCarry = vi.fn();
    render(<GapList gaps={gaps} explanations={explained(false)} onCarry={onCarry} />);

    fireEvent.click(within(card()).getByRole("button", { name: "다음 회의 어젠다로" }));

    expect(onCarry).toHaveBeenCalledWith(gapId, true);
    expect(within(card()).queryByText("다음 회의로 넘김")).toBeNull();
  });

  it("says a carried gap was sent on, and takes it back", () => {
    const onCarry = vi.fn();
    render(<GapList gaps={gaps} explanations={explained(true)} onCarry={onCarry} />);

    expect(within(card()).getByText("다음 회의로 넘김")).toBeTruthy();
    fireEvent.click(within(card()).getByRole("button", { name: "다음 회의에서 빼기" }));

    expect(onCarry).toHaveBeenCalledWith(gapId, false);
  });

  it("waits for the explanation, which is where the state comes from", () => {
    render(<GapList gaps={gaps} explanations={null} onCarry={vi.fn()} />);

    const button = within(card()).getByRole("button", { name: "다음 회의 어젠다로" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("is disabled while that gap's write is in flight", () => {
    render(
      <GapList gaps={gaps} explanations={explained(false)} onCarry={vi.fn()} pendingGapId={gapId} />,
    );

    const button = within(card()).getByRole("button", { name: "다음 회의 어젠다로" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("leaves 담당자 지정해 질문 disabled", () => {
    render(<GapList gaps={gaps} explanations={explained(false)} onCarry={vi.fn()} />);

    const ask = within(card()).getByRole("button", { name: "담당자 지정해 질문" });
    expect((ask as HTMLButtonElement).disabled).toBe(true);
  });
});
