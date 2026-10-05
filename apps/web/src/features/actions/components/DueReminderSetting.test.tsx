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

const box = () =>
  screen.getByRole("checkbox", { name: /마감 알림 받기/ }) as HTMLInputElement;
const found = async () =>
  (await screen.findByRole("checkbox", {
    name: /마감 알림 받기/,
  })) as HTMLInputElement;

describe("DueReminderSetting", () => {
  it("shows the person's own setting, on by default", async () => {
    get.mockResolvedValue({ on: true, sent_here: true });
    render(<DueReminderSetting />);
    expect((await found()).checked).toBe(true);
    expect(screen.queryByText(/아직 마감 알림을 보내지 않습니다/)).toBeNull();
  });

  it("turns them off with the person's choice and nothing else", async () => {
    get.mockResolvedValue({ on: true, sent_here: true });
    put.mockResolvedValue({ on: false, sent_here: true });
    render(<DueReminderSetting />);
    fireEvent.click(await found());
    await waitFor(() => expect(box().checked).toBe(false));
    expect(put).toHaveBeenCalledWith(false);
  });

  it("says so when this server sends no reminders yet", async () => {
    get.mockResolvedValue({ on: true, sent_here: false });
    render(<DueReminderSetting />);
    expect(
      await screen.findByText(/아직 마감 알림을 보내지 않습니다/),
    ).toBeTruthy();
  });

  it("keeps the old setting and says so when the change fails", async () => {
    get.mockResolvedValue({ on: true, sent_here: true });
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
