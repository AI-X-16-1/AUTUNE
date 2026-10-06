import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { GapList } from "./GapList";
import { DEMO_EXPLANATIONS, DEMO_REPORT } from "../fixtures/report-demo";
import type { GapExplanations } from "../types";

// Sending gaps on to the next meeting is "다음 회의 잡기" beside the template rail
// (TemplateRail.schedule.test.tsx, #824). The card only says a gap was sent on,
// and holds "담당자 지정해 질문" and "해당 없음".

const gaps = (DEMO_REPORT.gaps ?? []).slice(0, 1);
const gapId = gaps[0]!.id;

const explained = (carried: boolean): GapExplanations => ({
  ...DEMO_EXPLANATIONS,
  gaps: DEMO_EXPLANATIONS.gaps.map((e) => (e.gap_id === gapId ? { ...e, carried } : e)),
});

const card = () => screen.getAllByRole("article")[0]!;

afterEach(cleanup);

describe("GapList — a gap sent on to the next meeting", () => {
  it("says a carried gap was sent on", () => {
    render(<GapList gaps={gaps} explanations={explained(true)} />);

    expect(within(card()).getByText("다음 회의로 넘김")).toBeTruthy();
  });

  it("says nothing for a gap not sent on", () => {
    render(<GapList gaps={gaps} explanations={explained(false)} />);

    expect(within(card()).queryByText("다음 회의로 넘김")).toBeNull();
  });

  it("has no per-card button for it", () => {
    render(<GapList gaps={gaps} explanations={explained(false)} />);

    expect(within(card()).queryByRole("button", { name: "다음 회의 어젠다로" })).toBeNull();
  });

  it("leaves 담당자 지정해 질문 disabled without its handlers", () => {
    render(<GapList gaps={gaps} explanations={explained(false)} />);

    const ask = within(card()).getByRole("button", { name: "담당자 지정해 질문" });
    expect((ask as HTMLButtonElement).disabled).toBe(true);
  });
});
