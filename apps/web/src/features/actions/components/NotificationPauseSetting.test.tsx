import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NotificationPauseSetting } from "./NotificationPauseSetting";
import type { NotificationPause, NotificationPauseRead } from "../api";

// The person's own leave dates: no morning DM and no Monday digest between them.

const get = vi.fn<() => Promise<NotificationPauseRead>>();
const put = vi.fn<(pause: NotificationPause) => Promise<NotificationPause>>();
vi.mock("../api", () => ({
  getNotificationPause: () => get(),
  setNotificationPause: (pause: NotificationPause) => put(pause),
}));

afterEach(() => {
  cleanup();
  get.mockReset();
  put.mockReset();
});

const NONE = { starts_on: null, ends_on: null };
const WEEK = { starts_on: "2026-10-12", ends_on: "2026-10-16" };
const first = async () =>
  (await screen.findByLabelText("휴가 시작일")) as HTMLInputElement;
const last = () => screen.getByLabelText("휴가 종료일") as HTMLInputElement;
const save = () => screen.getByRole("button", { name: "저장" });

describe("NotificationPauseSetting", () => {
  it("shows no dates and no clear button when nothing is set", async () => {
    get.mockResolvedValue(NONE);
    render(<NotificationPauseSetting />);
    expect((await first()).value).toBe("");
    expect(screen.queryByRole("button", { name: "해제" })).toBeNull();
    expect((save() as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the person's own dates and says what they stop", async () => {
    get.mockResolvedValue(WEEK);
    render(<NotificationPauseSetting />);
    expect((await first()).value).toBe("2026-10-12");
    expect(last().value).toBe("2026-10-16");
    expect(
      screen.getByText(/2026-10-12부터 2026-10-16까지 보내지 않습니다/),
    ).toBeTruthy();
    expect(screen.getByText(/마감 알림은 그대로 갑니다/)).toBeTruthy();
  });

  it("says holidays are skipped, and the calendar only where this server reads it", async () => {
    get.mockResolvedValue(NONE);
    const { unmount } = render(<NotificationPauseSetting />);
    await first();
    expect(screen.getByText(/공휴일에는 보내지 않습니다/)).toBeTruthy();
    expect(screen.queryByText(/부재중/)).toBeNull();
    unmount();

    get.mockResolvedValue({ ...NONE, calendar_leave: true });
    render(<NotificationPauseSetting />);
    await first();
    // Of this read only: the same grant reads more for C's picker (#872).
    expect(screen.queryByText(/다른 일정은 읽지 않습니다/)).toBeNull();
    expect(screen.getByText(/이를 위해서는 부재중 일정의 시간만 읽습니다/)).toBeTruthy();
  });

  it("saves both days and nothing else", async () => {
    get.mockResolvedValue(NONE);
    put.mockResolvedValue(WEEK);
    render(<NotificationPauseSetting />);
    fireEvent.change(await first(), { target: { value: "2026-10-12" } });
    fireEvent.change(last(), { target: { value: "2026-10-16" } });
    fireEvent.click(save());
    await waitFor(() => expect(put).toHaveBeenCalledWith(WEEK));
    expect(await screen.findByRole("button", { name: "해제" })).toBeTruthy();
  });

  it("clears the dates with two nulls", async () => {
    get.mockResolvedValue(WEEK);
    put.mockResolvedValue(NONE);
    render(<NotificationPauseSetting />);
    await first();
    fireEvent.click(screen.getByRole("button", { name: "해제" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith(NONE));
    await waitFor(() => expect(last().value).toBe(""));
  });

  it("does not send a range that ends before it starts", async () => {
    get.mockResolvedValue(NONE);
    render(<NotificationPauseSetting />);
    fireEvent.change(await first(), { target: { value: "2026-10-16" } });
    fireEvent.change(last(), { target: { value: "2026-10-12" } });
    expect(screen.getByRole("alert").textContent).toContain("종료일");
    expect((save() as HTMLButtonElement).disabled).toBe(true);
    expect(put).not.toHaveBeenCalled();
  });

  it("keeps what was saved and says so when the change fails", async () => {
    get.mockResolvedValue(WEEK);
    put.mockRejectedValue(new Error("500"));
    render(<NotificationPauseSetting />);
    await first();
    fireEvent.click(screen.getByRole("button", { name: "해제" }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "다시 시도",
    );
    expect(last().value).toBe("2026-10-16");
  });

  it("shows nothing when the dates cannot be read", async () => {
    get.mockRejectedValue(new Error("500"));
    const { container } = render(<NotificationPauseSetting />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  // "내 Google 캘린더에도 추가" (the user, 2026-10-06): when someone is away is
  // theirs alone, so the dates reach a calendar only by their own tick.
  const box = () =>
    screen.queryByRole("checkbox", {
      name: "내 Google 캘린더에도 추가",
    }) as HTMLInputElement | null;
  const CONNECTED = { ...NONE, calendar_connected: true };

  it("offers the calendar only to a person whose calendar is connected", async () => {
    get.mockResolvedValue(NONE);
    render(<NotificationPauseSetting />);
    await first();
    expect(box()).toBeNull();
    expect(screen.getByText(/연결하면 이 기간을 캘린더에도 넣을지/)).toBeTruthy();
  });

  it("leaves the box unticked, and says what a tick writes before it is ticked", async () => {
    get.mockResolvedValue(CONNECTED);
    render(<NotificationPauseSetting />);
    await first();
    expect(box()?.checked).toBe(false);
    const line = screen.getByText(/종일\s+일정으로 들어갑니다/).textContent ?? "";
    expect(line).toContain("휴가");
    expect(line).toContain("비공개 일정");
    expect(line).toContain("바쁘다는 것만");
    expect(line).toContain("그 일정을");
    expect(line).toContain("기간이 지난 일정은 내 캘린더에 그대로 남습니다");
  });

  it("sends the tick only when it was ticked, and says the event went", async () => {
    get.mockResolvedValue(CONNECTED);
    put.mockResolvedValue({
      ...WEEK,
      on_calendar: true,
      calendar_connected: true,
      calendar: "added",
    } as NotificationPauseRead);
    render(<NotificationPauseSetting />);
    fireEvent.change(await first(), { target: { value: "2026-10-12" } });
    fireEvent.change(last(), { target: { value: "2026-10-16" } });
    fireEvent.click(box()!);
    fireEvent.click(save());
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith({ ...WEEK, on_calendar: true }),
    );
    expect(
      (await screen.findByRole("status")).textContent,
    ).toBe("내 Google 캘린더에 휴가 일정을 넣었습니다.");
    expect(box()?.checked).toBe(true);
    expect(screen.getByText(/내 Google 캘린더에도 휴가 일정이 들어가 있습니다/)).toBeTruthy();
  });

  it("sends an unticked box as false, so an earlier event is taken off", async () => {
    get.mockResolvedValue({ ...WEEK, on_calendar: true, calendar_connected: true });
    put.mockResolvedValue({
      ...WEEK,
      on_calendar: false,
      calendar_connected: true,
      calendar: "removed",
    } as NotificationPauseRead);
    render(<NotificationPauseSetting />);
    await first();
    expect(box()?.checked).toBe(true);
    fireEvent.click(box()!);
    fireEvent.click(save());
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith({ ...WEEK, on_calendar: false }),
    );
    expect(
      (await screen.findByRole("status")).textContent,
    ).toBe("내 Google 캘린더에서 휴가 일정을 지웠습니다.");
    expect(box()?.checked).toBe(false);
  });

  it.each([
    ["failed", "캘린더에는 넣지 못했으니"],
    ["not_connected", "캘린더가 연결되어 있지 않아"],
    ["removal_queued", "지금은 지우지 못했습니다"],
    ["not_removed", "이전 휴가 일정을 지우지 못했습니다. 캘린더에서 직접 지워 주세요."],
  ] as const)(
    "says the dates are kept when the calendar answered %s",
    async (calendar, words) => {
      get.mockResolvedValue(CONNECTED);
      put.mockResolvedValue({
        ...WEEK,
        calendar_connected: true,
        calendar,
      } as NotificationPauseRead);
      render(<NotificationPauseSetting />);
      fireEvent.change(await first(), { target: { value: "2026-10-12" } });
      fireEvent.change(last(), { target: { value: "2026-10-16" } });
      fireEvent.click(box()!);
      fireEvent.click(save());
      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toContain(words);
      expect((await first()).value).toBe("2026-10-12");
      expect(screen.queryByText(/내 Google 캘린더에도 휴가 일정이 들어가 있습니다/)).toBeNull();
    },
  );

  it("sends no tick from a screen that drew no box, and does not say the new range is on the calendar", async () => {
    // Their calendar is not connected just now, and an event of theirs
    // stands from before: the server keeps it (the field is left out, not
    // false) and answers that the calendar could not follow.
    get.mockResolvedValue({ ...WEEK, on_calendar: true });
    put.mockResolvedValue({
      starts_on: "2026-10-19",
      ends_on: "2026-10-23",
      on_calendar: true,
      calendar: "not_connected",
    } as NotificationPauseRead);
    render(<NotificationPauseSetting />);
    fireEvent.change(await first(), { target: { value: "2026-10-19" } });
    fireEvent.change(last(), { target: { value: "2026-10-23" } });
    expect(box()).toBeNull();
    fireEvent.click(save());

    await waitFor(() =>
      expect(put).toHaveBeenCalledWith({
        starts_on: "2026-10-19",
        ends_on: "2026-10-23",
      }),
    );
    expect((await screen.findByRole("alert")).textContent).toContain(
      "캘린더가 연결되어 있지 않아 캘린더에는 넣지 못했습니다",
    );
    expect(
      screen.getByText(/내 Google 캘린더에도 휴가 일정이 들어가 있습니다/),
    ).toBeTruthy();
    expect(screen.queryByText(/이 기간은 내 Google 캘린더/)).toBeNull();
  });

  it("says nothing about a calendar after a save that touched none", async () => {
    get.mockResolvedValue(CONNECTED);
    put.mockResolvedValue({
      ...WEEK,
      calendar_connected: true,
      calendar: "off",
    } as NotificationPauseRead);
    render(<NotificationPauseSetting />);
    fireEvent.change(await first(), { target: { value: "2026-10-12" } });
    fireEvent.change(last(), { target: { value: "2026-10-16" } });
    fireEvent.click(save());
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith({ ...WEEK, on_calendar: false }),
    );
    await screen.findByRole("button", { name: "해제" });
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
