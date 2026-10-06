import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GapList } from "./GapList";
import { DEMO_EXPLANATIONS, DEMO_REPORT } from "../fixtures/report-demo";
import type { GapAskTargets } from "../types";

// "담당자 지정해 질문" (#824): a teammate picked by hand, and the question put on
// their Google Calendar. Someone with no calendar connected cannot be picked.

const gaps = (DEMO_REPORT.gaps ?? []).slice(0, 1);
const gapId = gaps[0]!.id;

const targets: GapAskTargets = {
  gap_id: gapId,
  members: [
    { user_id: "usr_a", name: "김하나", calendar_connected: true, asked: false },
    { user_id: "usr_b", name: "이둘", calendar_connected: false, asked: false },
    { user_id: "usr_c", name: "박셋", calendar_connected: true, asked: true },
  ],
};

const card = () => screen.getAllByRole("article")[0]!;

afterEach(cleanup);

function renderList(onAsk = vi.fn()) {
  const load = vi.fn().mockResolvedValue(targets);
  render(
    <GapList gaps={gaps} explanations={DEMO_EXPLANATIONS} loadAskTargets={load} onAsk={onAsk} />,
  );
  fireEvent.click(within(card()).getByRole("button", { name: "담당자 지정해 질문" }));
  return { load, onAsk };
}

describe("GapList — 담당자 지정해 질문", () => {
  it("lists the team when opened, and sends to the one picked", async () => {
    const { load, onAsk } = renderList();

    const select = await within(card()).findByLabelText("질문할 담당자");
    expect(load).toHaveBeenCalledWith(gapId);
    fireEvent.change(select, { target: { value: "usr_a" } });
    fireEvent.click(within(card()).getByRole("button", { name: "캘린더에 질문 넣기" }));

    expect(onAsk).toHaveBeenCalledWith(gapId, "usr_a");
  });

  it("will not send before someone is picked", async () => {
    renderList();

    await within(card()).findByLabelText("질문할 담당자");
    const send = within(card()).getByRole("button", { name: "캘린더에 질문 넣기" });
    expect((send as HTMLButtonElement).disabled).toBe(true);
  });

  it("says who has no calendar, and who was already asked", async () => {
    renderList();

    await within(card()).findByLabelText("질문할 담당자");
    const noCalendar = within(card()).getByRole("option", { name: /이둘/ }) as HTMLOptionElement;
    expect(noCalendar.disabled).toBe(true);
    expect(noCalendar.textContent).toContain("캘린더 미연결");
    expect(within(card()).getByRole("option", { name: /박셋/ }).textContent).toContain("질문함");
  });
});
