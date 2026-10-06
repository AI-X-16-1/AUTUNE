import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getCalendarConnection } from "@/shared/api/auth";

import { TemplateRail } from "./TemplateRail";
import { DEMO_COMPARISON } from "../fixtures/report-demo";
import type { GapAgendaEvents } from "../types";

// "다음 회의 잡기" (#824): beside the rail's heading, for the whole meeting. It
// opens the caller's own calendar, or the way to connect it.

vi.mock("@/shared/api/auth", () => ({
  getCalendarConnection: vi.fn(),
  googleCalendarConnectUrl: (to: string) => `/api/auth/google/calendar/start?redirect_to=${to}`,
}));

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

  it("says why instead of leaving the page when there is no session to connect with", async () => {
    vi.mocked(getCalendarConnection).mockResolvedValue(null);
    renderRail({ calendar: "not_connected", events: [] });

    open();
    fireEvent.click(await screen.findByRole("button", { name: "Google 캘린더 연결" }));

    expect((await screen.findByRole("alert")).textContent).toContain("로그인 세션이 없어");
  });

  it("reopens saying so when the connect came back failed", async () => {
    window.history.replaceState(null, "", "/meetings/mtg_1/gap?calendar=failed");
    renderRail({ calendar: "not_connected", events: [] });

    expect((await screen.findByRole("alert")).textContent).toContain("연결하지 못했습니다");
    expect(window.location.search).toBe("");
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
