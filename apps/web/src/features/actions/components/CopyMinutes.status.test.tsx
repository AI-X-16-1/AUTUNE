import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { COPIED_FOR, CopyMinutes } from "./CopyMinutes";
import type { MeetingSummary } from "../types";

// "복사했습니다." is about one copy (review of #752). Left standing, it went on
// saying so after the meeting's content had changed, and nobody could tell
// whether the page they held was the new one.

const SUMMARY: MeetingSummary = {
  meeting_id: "mtg_1",
  decisions: [{ id: "dec_1", statement: "배포는 다음 주 화요일에 한다", status: "confirmed" }],
  action_items: [],
  open_questions: 0,
  ambiguous_waiting: 0,
  note: null,
  note_updated_at: null,
};

const CHANGED: MeetingSummary = {
  ...SUMMARY,
  decisions: [{ id: "dec_1", statement: "배포는 다음 주 목요일에 한다", status: "confirmed" }],
};

const status = () => screen.queryByRole("status");

async function copy() {
  fireEvent.click(screen.getByRole("button", { name: "회의록 복사" }));
  // The clipboard answers in a microtask; timers are faked, so wait by hand.
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("navigator", {
    ...navigator,
    clipboard: { writeText: vi.fn(() => Promise.resolve()) },
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the copied notice", () => {
  it("goes away by itself after a few seconds", async () => {
    render(<CopyMinutes summary={SUMMARY} />);
    await copy();
    expect(status()?.textContent).toBe("복사했습니다.");

    act(() => {
      vi.advanceTimersByTime(COPIED_FOR - 1);
    });
    expect(status()).not.toBeNull();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(status()).toBeNull();
  });

  it("goes at once when the page on screen is no longer the one that was copied", async () => {
    const { rerender } = render(<CopyMinutes summary={SUMMARY} />);
    await copy();
    expect(status()).not.toBeNull();

    rerender(<CopyMinutes summary={CHANGED} />);

    expect(status()).toBeNull();
  });

  it("counts from the latest copy, not the first", async () => {
    render(<CopyMinutes summary={SUMMARY} />);
    await copy();
    act(() => {
      vi.advanceTimersByTime(COPIED_FOR - 1000);
    });

    await copy();
    act(() => {
      vi.advanceTimersByTime(COPIED_FOR - 1000);
    });

    // The first copy's timer would have cleared it a second in.
    expect(status()).not.toBeNull();
  });

  it("leaves no timer running after the tab is left", async () => {
    const { unmount } = render(<CopyMinutes summary={SUMMARY} />);
    await copy();

    unmount();

    expect(vi.getTimerCount()).toBe(0);
  });
});
