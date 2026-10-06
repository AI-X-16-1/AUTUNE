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
    expect(line).toContain("어느 일정에 적었는지만");
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
});
