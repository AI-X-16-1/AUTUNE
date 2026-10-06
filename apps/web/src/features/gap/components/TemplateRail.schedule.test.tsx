import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TemplateRail } from "./TemplateRail";
import { DEMO_COMPARISON } from "../fixtures/report-demo";
import type { GapAgendaEvents } from "../types";

// "다음 회의 잡기" (#824): beside the rail's heading, for the whole meeting. It
// opens the caller's own calendar, or the way to connect it.

afterEach(cleanup);

const open = () => fireEvent.click(screen.getByRole("button", { name: "다음 회의 잡기" }));

const connected: GapAgendaEvents = {
  calendar: "ok",
  events: [
    { id: "evt_1", summary: "주간 회의", start: "2026-10-08T05:00:00Z", end: null },
    { id: "evt_2", summary: "1:1", start: "2026-10-09T01:00:00Z", end: null },
  ],
};

function renderRail(events: GapAgendaEvents, onScheduleNext = vi.fn()) {
  const load = vi.fn().mockResolvedValue(events);
  render(
    <TemplateRail
      comparison={DEMO_COMPARISON}
      loadAgendaEvents={load}
      onScheduleNext={onScheduleNext}
    />,
  );
  return { load, onScheduleNext };
}

describe("TemplateRail — 다음 회의 잡기", () => {
  it("opens my calendar and puts the gaps on the event I pick", async () => {
    const { load, onScheduleNext } = renderRail(connected);

    open();
    fireEvent.click(await screen.findByRole("radio", { name: /주간 회의/ }));
    fireEvent.click(screen.getByRole("button", { name: "이 일정에 갭 넣기" }));

    expect(load).toHaveBeenCalledOnce();
    expect(onScheduleNext).toHaveBeenCalledWith("evt_1");
  });

  it("will not put them anywhere before an event is picked", async () => {
    renderRail(connected);

    open();
    await screen.findByRole("radio", { name: /주간 회의/ });
    const put = screen.getByRole("button", { name: "이 일정에 갭 넣기" }) as HTMLButtonElement;
    expect(put.disabled).toBe(true);
  });

  it("offers to connect a calendar when none is connected", async () => {
    const { onScheduleNext } = renderRail({ calendar: "not_connected", events: [] });

    open();

    expect(await screen.findByRole("button", { name: "Google 캘린더 연결" })).toBeTruthy();
    expect(onScheduleNext).not.toHaveBeenCalled();
  });

  it("is not drawn without its handlers", () => {
    render(<TemplateRail comparison={DEMO_COMPARISON} />);

    expect(screen.queryByRole("button", { name: "다음 회의 잡기" })).toBeNull();
  });

  it("waits for a meeting that has been compared", () => {
    render(
      <TemplateRail
        comparison={{ ...DEMO_COMPARISON, analysed: false }}
        loadAgendaEvents={vi.fn()}
        onScheduleNext={vi.fn()}
      />,
    );

    const button = screen.getByRole("button", { name: "다음 회의 잡기" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });
});
