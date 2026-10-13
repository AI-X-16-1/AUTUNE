import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CalendarConnect } from "./CalendarConnect";

// What the calendar connection is asked for is said where it is asked
// (review of #838): due dates always, and out-of-office time only where this
// server reads it -- before the person connects, not after.

type Connection = { connected: boolean; needs_reconnect?: boolean } | null;
const connection = vi.fn<() => Promise<Connection>>();
vi.mock("@/shared/api/auth", () => ({
  getCalendarConnection: () => connection(),
  disconnectCalendar: vi.fn(),
  googleCalendarConnectUrl: (to: string) => `/api/auth/google/calendar/start?redirect_to=${to}`,
}));

type Pause = { starts_on: string | null; ends_on: string | null; calendar_leave?: boolean };
const pause = vi.fn<() => Promise<Pause>>();
vi.mock("../api", () => ({ getNotificationPause: () => pause() }));

afterEach(() => {
  cleanup();
  connection.mockReset();
  pause.mockReset();
});

const NONE = { starts_on: null, ends_on: null };
const NOTICE = /부재중.*일정이 언제부터/;
const PICKER = /다음 회의 잡기/;
const READ_BACK = /다른 날로 옮기면/;
const connectButton = () => screen.findByRole("button", { name: "내 Google 캘린더에 마감일 넣기" });

describe("CalendarConnect", () => {
  it("says nothing about out-of-office time where this server does not read it", async () => {
    connection.mockResolvedValue({ connected: false });
    pause.mockResolvedValue({ ...NONE, calendar_leave: false });
    render(<CalendarConnect />);

    await connectButton();
    await waitFor(() => expect(pause).toHaveBeenCalled());
    expect(screen.queryByText(NOTICE)).toBeNull();
  });

  it("says nothing when the server does not say either way", async () => {
    // An answer without the field -- a server from before it existed -- is
    // not a server that reads it.
    connection.mockResolvedValue({ connected: false });
    pause.mockResolvedValue(NONE);
    render(<CalendarConnect />);

    await connectButton();
    await waitFor(() => expect(pause).toHaveBeenCalled());
    expect(screen.queryByText(NOTICE)).toBeNull();
  });

  it("says before the person connects that out-of-office time is read too", async () => {
    connection.mockResolvedValue({ connected: false });
    pause.mockResolvedValue({ ...NONE, calendar_leave: true });
    render(<CalendarConnect />);

    await connectButton();
    const notice = await screen.findByText(NOTICE);
    expect(notice.textContent).toMatch(/^연결하면 /);
    expect(notice.textContent).toContain("이때는 일정의 제목이나 부재중이 아닌 일정은 받지 않으며");
    expect(notice.textContent).toContain("저장하지");
  });

  it("does not say of the whole connection that no title and no other event is read", async () => {
    // Review of #872: C's "다음 회의 잡기" reads event titles with the same
    // grant, so the sentence that was true of the out-of-office read alone
    // would have been false of the connection.
    connection.mockResolvedValue({ connected: false });
    pause.mockResolvedValue({ ...NONE, calendar_leave: true });
    const { container } = render(<CalendarConnect />);

    await screen.findByText(NOTICE);
    expect(container.textContent).not.toContain("일정의 제목이나 다른 일정은 읽지");
  });

  it("says before the person connects that a date moved in the calendar comes back", async () => {
    connection.mockResolvedValue({ connected: false });
    pause.mockResolvedValue({ ...NONE, calendar_leave: false });
    render(<CalendarConnect />);

    await connectButton();
    const line = (await screen.findByText(READ_BACK)).textContent ?? "";
    expect(line).toContain("그 할 일의 마감일도 10분쯤 안에 그 날로 바뀝니다");
    // Where the moved date then goes is said, not only that it is read.
    expect(line).toContain("팀 보드에 보이고");
    expect(line).toContain("팀이 Notion을 연결했다면 Notion에도 반영됩니다");
    // About this read only: the picker below does read other events.
    expect(line).toContain("이를 위해 Autune이 넣은 일정만 다시 읽으며");
  });

  it.each([
    ["to somebody already connected", { connected: true }, { ...NONE, calendar_leave: true }],
    ["when the server cannot be asked about out-of-office time", { connected: false }, null],
  ])("says it %s", async (_when, state, paused) => {
    connection.mockResolvedValue(state);
    if (paused) pause.mockResolvedValue(paused);
    else pause.mockRejectedValue(new Error("500"));
    render(<CalendarConnect />);

    expect(await screen.findByText(READ_BACK)).toBeTruthy();
  });

  it("says what pressing '다음 회의 잡기' reads and writes, before the person connects", async () => {
    connection.mockResolvedValue({ connected: false });
    pause.mockResolvedValue({ ...NONE, calendar_leave: false });
    render(<CalendarConnect />);

    await connectButton();
    const line = (await screen.findByText(PICKER)).textContent ?? "";
    expect(line).toContain("직접 누를 때에만");
    expect(line).toContain("앞으로 2주 일정(제목과 시간)");
    expect(line).toContain("설명과 참석자 주소를 읽어");
    expect(line).toContain("갭 질문을 적습니다");
    // Done to other people with this grant, so said before it is given (#872).
    expect(line).toContain("이미 초대된 사람들에게 Google이 내");
    expect(line).toContain("일정 변경 알림을 보냅니다");
    // What is kept is said, and not folded into "nothing is stored".
    expect(line).toContain("읽은 일정 목록·설명·주소는 저장하지 않습니다.");
    expect(line).toContain("어느 일정에 적었는지는 나중에 지울 수 있도록 기록하고");
    // The event's day is kept too, and somebody else is shown it with a name.
    expect(line).not.toContain("적었는지만");
    expect(line).toContain("그 일정이 시작하는 날짜도 함께 기록합니다");
    expect(line).toContain("후속 조치를 승인하는 사람에게 다음 회의 날짜 후보로");
    expect(line).toContain("고른 사람의 이름과 함께 보입니다");
  });

  it("says it to somebody already connected, and where out-of-office time is read too", async () => {
    connection.mockResolvedValue({ connected: true });
    pause.mockResolvedValue({ ...NONE, calendar_leave: true });
    render(<CalendarConnect />);

    expect(await screen.findByText("내 Google 캘린더 연결됨")).toBeTruthy();
    expect(await screen.findByText(PICKER)).toBeTruthy();
    expect(await screen.findByText(NOTICE)).toBeTruthy();
  });

  it("says it when the server cannot be asked about out-of-office time", async () => {
    connection.mockResolvedValue({ connected: false });
    pause.mockRejectedValue(new Error("500"));
    render(<CalendarConnect />);

    await connectButton();
    expect(await screen.findByText(PICKER)).toBeTruthy();
  });

  it("says it to somebody who connected earlier as well", async () => {
    connection.mockResolvedValue({ connected: true });
    pause.mockResolvedValue({ ...NONE, calendar_leave: true });
    render(<CalendarConnect />);

    expect(await screen.findByText("내 Google 캘린더 연결됨")).toBeTruthy();
    // Already connected: it is being read now, not "if you connect".
    expect((await screen.findByText(NOTICE)).textContent).toMatch(/^연결되어 있는 동안 /);
  });

  it("leaves the line off, and the button working, when it cannot ask", async () => {
    connection.mockResolvedValue({ connected: false });
    pause.mockRejectedValue(new Error("500"));
    render(<CalendarConnect />);

    await connectButton();
    await waitFor(() => expect(pause).toHaveBeenCalled());
    expect(screen.queryByText(NOTICE)).toBeNull();
  });

  it("shows nothing at all to a visitor with no session", async () => {
    connection.mockResolvedValue(null);
    pause.mockResolvedValue({ ...NONE, calendar_leave: true });
    const { container } = render(<CalendarConnect />);

    await waitFor(() => expect(pause).toHaveBeenCalled());
    await waitFor(() => expect(connection).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  it.each([
    ["before the person connects", false],
    ["to somebody already connected", true],
  ])("says what a ticked 휴가 기간 writes, %s", async (_when, connected) => {
    // A use of the grant is said where the grant is asked for (#838, #872);
    // the leave event is one (the user, 2026-10-06).
    connection.mockResolvedValue({ connected });
    pause.mockResolvedValue({ ...NONE, calendar_leave: false });
    render(<CalendarConnect />);

    const line = (await screen.findByText(/휴가 기간/)).textContent ?? "";
    expect(line).toContain("직접 체크하고");
    expect(line).toContain("저장할 때에만");
    expect(line).toContain("비공개 종일 일정");
    expect(line).toContain("그 일정을 지우며");
    expect(line).toContain("기간이 지난 일정은 내");
    expect(line).toContain("다른 일정을 읽지는 않습니다");
  });

  it("says it when the server cannot be asked about the pause either", async () => {
    connection.mockResolvedValue({ connected: false });
    pause.mockRejectedValue(new Error("down"));
    render(<CalendarConnect />);

    await connectButton();
    expect(await screen.findByText(/휴가 기간/)).toBeTruthy();
  });

  // jsdom lays nothing out, so this is the rule and not the result: the page
  // being no wider than a 390 px screen was measured in a browser. What is
  // held here is that the label is not put back on one line at a fixed height,
  // which is what made the page 9 px too wide.
  it("lets the connect button's label take a second line where the column is narrow", async () => {
    connection.mockResolvedValue({ connected: false });
    pause.mockResolvedValue(NONE);
    render(<CalendarConnect />);

    const rules = (await connectButton()).className.split(/\s+/);
    // Over the shared button's own `whitespace-nowrap` and inline height.
    expect(rules).toContain("whitespace-normal!");
    expect(rules).toContain("h-auto!");
    expect(rules).toContain("max-w-full");
    // And no shorter than it was where the label fits on one line.
    expect(rules).toContain("min-h-[var(--control-h-compact)]");
  });
});
