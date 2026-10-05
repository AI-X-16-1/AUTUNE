import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NotificationPauseSetting } from "./NotificationPauseSetting";
import type { NotificationPause } from "../api";

// The person's own leave dates: no morning DM and no Monday digest between them.

const get = vi.fn<() => Promise<NotificationPause>>();
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
});
