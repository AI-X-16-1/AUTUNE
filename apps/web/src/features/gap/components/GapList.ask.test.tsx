import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { meetingCarryNotice } from "../hooks/useGapActions";
import { GapList } from "./GapList";
import { DEMO_EXPLANATIONS, DEMO_REPORT } from "../fixtures/report-demo";

// "담당자 지정해 질문" (#824) posts the question on the team's Slack channel,
// mentioning the member picked. It writes nobody's calendar: one person's grant
// is for their own work only (mkkim68 on #824).

const gaps = (DEMO_REPORT.gaps ?? []).slice(0, 1);

afterEach(cleanup);

describe("GapList — 담당자 지정해 질문", () => {
  it("picks a member and sends the question to Slack", async () => {
    const gapId = gaps[0]!.id;
    const loadAskTargets = vi.fn().mockResolvedValue({
      gap_id: gapId,
      members: [
        { user_id: "usr_a", name: "김민경" },
        { user_id: "usr_b", name: "강민구" },
      ],
    });
    const onAsk = vi.fn();
    render(
      <GapList
        gaps={gaps}
        explanations={DEMO_EXPLANATIONS}
        onDismiss={() => undefined}
        loadAskTargets={loadAskTargets}
        onAsk={onAsk}
      />,
    );

    const card = screen.getAllByRole("article")[0]!;
    fireEvent.click(within(card).getByRole("button", { name: "담당자 지정해 질문" }));
    const select = await screen.findByLabelText("질문할 담당자");
    fireEvent.change(select, { target: { value: "usr_b" } });
    fireEvent.click(screen.getByRole("button", { name: "Slack으로 질문 보내기" }));

    await waitFor(() => expect(onAsk).toHaveBeenCalledWith(gapId, "usr_b"));
    expect(loadAskTargets).toHaveBeenCalledWith(gapId);
  });

  it("is drawn disabled without a way to ask", () => {
    render(<GapList gaps={gaps} explanations={DEMO_EXPLANATIONS} onDismiss={() => undefined} />);

    const card = screen.getAllByRole("article")[0]!;
    const ask = within(card).getByRole("button", { name: "담당자 지정해 질문" });
    expect((ask as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("다음 회의 잡기 — what the screen says", () => {
  it("says why nothing went onto the calendar", () => {
    const notice = meetingCarryNotice({
      meeting_id: "mtg_1",
      carried: 2,
      calendar: "external_attendees",
      slack: "not_tried",
    });

    expect(notice).toContain("갭 2건을 다음 회의로 넘겼습니다.");
    expect(notice).toContain("팀 밖 참석자가 있는 일정");
    expect(notice).not.toContain("Slack");
  });

  it("says the team channel was told", () => {
    const notice = meetingCarryNotice({
      meeting_id: "mtg_1",
      carried: 1,
      calendar: "added",
      slack: "posted",
    });

    expect(notice).toBe(
      "갭 1건을 다음 회의로 넘겼습니다. 다음 회의 일정 설명에 추가했습니다. 팀 Slack 채널에 공지했습니다.",
    );
  });
});
