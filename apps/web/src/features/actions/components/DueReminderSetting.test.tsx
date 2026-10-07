import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DueReminderSetting } from "./DueReminderSetting";
import type { DueReminderSetting as Setting } from "../api";

// The person's own switch for due-date reminders (review of #751).

const get = vi.fn<() => Promise<Setting>>();
const put = vi.fn<(on: boolean) => Promise<Setting>>();
vi.mock("../api", () => ({
  getDueReminders: () => get(),
  setDueReminders: (on: boolean) => put(on),
}));

afterEach(() => {
  cleanup();
  get.mockReset();
  put.mockReset();
});

const ALL: Setting = {
  on: true,
  sent_here: true,
  weekly_here: true,
  daily_here: true,
  after_meeting_here: true,
};

const box = () =>
  screen.getByRole("checkbox", { name: /마감 알림 받기/ }) as HTMLInputElement;
const found = async () =>
  (await screen.findByRole("checkbox", {
    name: /마감 알림 받기/,
  })) as HTMLInputElement;

describe("DueReminderSetting", () => {
  it("shows the person's own setting, on by default", async () => {
    get.mockResolvedValue(ALL);
    render(<DueReminderSetting />);
    expect((await found()).checked).toBe(true);
    expect(screen.queryByText(/보내지 않/)).toBeNull();
  });

  it("says the Monday digest moves when Monday is a public holiday", async () => {
    get.mockResolvedValue(ALL);
    render(<DueReminderSetting />);
    expect((await found()).parentElement?.textContent).toContain(
      "월요일이 공휴일이면 그 주의 첫 평일",
    );
  });

  it("turns them off with the person's choice and nothing else", async () => {
    get.mockResolvedValue(ALL);
    put.mockResolvedValue({ ...ALL, on: false });
    render(<DueReminderSetting />);
    fireEvent.click(await found());
    await waitFor(() => expect(box().checked).toBe(false));
    expect(put).toHaveBeenCalledWith(false);
  });

  it("says so when this server sends none of the three yet", async () => {
    get.mockResolvedValue({
      ...ALL,
      sent_here: false,
      weekly_here: false,
      daily_here: false,
      after_meeting_here: false,
    });
    render(<DueReminderSetting />);
    expect(await screen.findByText(/이 서버는 아직 이 알림들을 보내지 않습니다/)).toBeTruthy();
  });

  it("says which it sends and which it does not when a server sends only some", async () => {
    // dev, 2026-10-05: the digests were on and the reminder off, and the line
    // read "this server sends no reminders yet" under a switch for all three.
    get.mockResolvedValue({ ...ALL, sent_here: false });
    render(<DueReminderSetting />);
    const line = (await screen.findByText(/이 서버는 지금/)).textContent;
    expect(line).toContain("월요일 요약, 아침 요약, 회의 직후 알림만 보냅니다");
    expect(line).toContain("아직 보내지 않는 것: 마감 알림.");
  });

  it("names the notice after a meeting among what the switch governs", async () => {
    get.mockResolvedValue({ ...ALL, after_meeting_here: false });
    render(<DueReminderSetting />);
    expect((await found()).parentElement?.textContent).toContain("직후 내 담당 항목 알림");
    expect((await screen.findByText(/이 서버는 지금/)).textContent).toContain(
      "아직 보내지 않는 것: 회의 직후 알림.",
    );
  });

  it("keeps the old setting and says so when the change fails", async () => {
    get.mockResolvedValue(ALL);
    put.mockRejectedValue(new Error("500"));
    render(<DueReminderSetting />);
    fireEvent.click(await found());
    expect((await screen.findByRole("alert")).textContent).toContain(
      "다시 시도",
    );
    expect(box().checked).toBe(true);
  });

  it("shows nothing when the setting cannot be read", async () => {
    get.mockRejectedValue(new Error("500"));
    const { container } = render(<DueReminderSetting />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });
});
