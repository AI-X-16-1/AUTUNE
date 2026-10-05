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
    expect(notice.textContent).toContain("제목이나 다른 일정은 읽지 않으며");
    expect(notice.textContent).toContain("저장하지");
  });

  it("says it to somebody who connected earlier as well", async () => {
    connection.mockResolvedValue({ connected: true });
    pause.mockResolvedValue({ ...NONE, calendar_leave: true });
    render(<CalendarConnect />);

    expect(await screen.findByText("내 Google 캘린더 연결됨")).toBeTruthy();
    expect(await screen.findByText(NOTICE)).toBeTruthy();
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
