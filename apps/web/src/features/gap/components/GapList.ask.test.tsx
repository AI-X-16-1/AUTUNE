import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { meetingCarryNotice } from "../hooks/useGapActions";
import { GapList } from "./GapList";
import { DEMO_EXPLANATIONS, DEMO_REPORT } from "../fixtures/report-demo";

// "담당자 지정해 질문" (#824) does not write to a teammate's calendar: one
// person's grant is for their own work only (mkkim68 on #824). The button stays
// drawn and disabled until it comes as a mention on the team channel's card.

const gaps = (DEMO_REPORT.gaps ?? []).slice(0, 1);

afterEach(cleanup);

describe("GapList — 담당자 지정해 질문", () => {
  it("is drawn disabled, and the list says it is not ready", () => {
    render(<GapList gaps={gaps} explanations={DEMO_EXPLANATIONS} onDismiss={() => undefined} />);

    const card = screen.getAllByRole("article")[0]!;
    const ask = within(card).getByRole("button", { name: "담당자 지정해 질문" });
    expect((ask as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/아직 준비 중이라 누를 수 없습니다/)).toBeTruthy();
  });
});

describe("다음 회의 잡기 — an event shared outside the team", () => {
  it("says why nothing went onto the calendar", () => {
    const notice = meetingCarryNotice({
      meeting_id: "mtg_1",
      carried: 2,
      calendar: "external_attendees",
    });

    expect(notice).toContain("갭 2건을 다음 회의로 넘겼습니다.");
    expect(notice).toContain("팀 밖 참석자가 있는 일정");
  });
});
