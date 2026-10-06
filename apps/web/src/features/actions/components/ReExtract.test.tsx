import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { POLL_MS, ReExtract } from "./ReExtract";
import type { ExtractionState } from "../api";

// The 액션 tab says when a meeting's extraction failed, and runs it again on
// request (the user, 2026-10-06). The request is accepted at once and run by
// the worker, so the screen looks again until the run has left a trace.

const get = vi.fn<(meetingId: string) => Promise<ExtractionState>>();
const post = vi.fn<(meetingId: string) => Promise<ExtractionState>>();
vi.mock("../api", () => ({
  getExtractionState: (meetingId: string) => get(meetingId),
  requestExtraction: (meetingId: string) => post(meetingId),
}));

const FINE: ExtractionState = {
  extracted_at: "2026-10-05T11:00:00Z",
  failures: 0,
  failed_at: null,
  will_retry: false,
  requested: false,
  requested_at: null,
};
const RETRYING: ExtractionState = {
  ...FINE,
  extracted_at: null,
  failures: 1,
  failed_at: "2026-10-05T14:24:41Z",
  will_retry: true,
};
const SPENT: ExtractionState = { ...RETRYING, failures: 3, will_retry: false };

const extracted = vi.fn();
const settle = async (ms = 0) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};
const button = () => screen.getByRole("button", { name: "액션·결정 다시 추출" }) as HTMLButtonElement;

async function open(state: ExtractionState) {
  get.mockResolvedValueOnce(state);
  render(<ReExtract meetingId="mtg_1" onExtracted={extracted} />);
  await settle();
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  get.mockReset();
  post.mockReset();
  extracted.mockReset();
});

describe("ReExtract", () => {
  it("offers the button and says nothing else about a meeting that was extracted", async () => {
    await open(FINE);

    expect(button().disabled).toBe(false);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(/액션 목록을 그대로 두고/)).toBeTruthy();
  });

  it("says the server is still trying while tries are left", async () => {
    await open(RETRYING);

    expect(screen.getByRole("status").textContent).toContain("자동으로 다시 시도하고 있습니다");
    expect(screen.getByRole("status").textContent).toContain("1번 실패");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says the extraction failed once the tries are spent", async () => {
    await open(SPENT);

    const alert = screen.getByRole("alert").textContent;
    expect(alert).toContain("추출하지 못했습니다(3번 시도)");
    expect(alert).toContain("자동으로는 더 시도하지 않습니다");
  });

  it("asks, waits for the worker, and has the board read again when the run is in", async () => {
    await open(SPENT);
    post.mockResolvedValue({ ...SPENT, requested: true, requested_at: "2026-10-06T01:00:00Z" });

    fireEvent.click(button());
    await settle();

    expect(post).toHaveBeenCalledExactlyOnceWith("mtg_1");
    expect(button().disabled).toBe(true);
    expect(screen.getByText(/다시 추출을 요청했습니다/)).toBeTruthy();

    // Taken by the worker and still running: no trace yet.
    get.mockResolvedValueOnce({ ...SPENT, requested_at: "2026-10-06T01:00:00Z" });
    await settle(POLL_MS);
    expect(button().disabled).toBe(true);
    expect(extracted).not.toHaveBeenCalled();

    get.mockResolvedValueOnce({ ...FINE, extracted_at: "2026-10-06T01:01:30Z" });
    await settle(POLL_MS);

    expect(extracted).toHaveBeenCalledOnce();
    expect(button().disabled).toBe(false);
    expect(screen.getByText("다시 추출했습니다.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says so when the requested run failed too, and leaves the board alone", async () => {
    await open(SPENT);
    post.mockResolvedValue({ ...SPENT, requested: true });

    fireEvent.click(button());
    await settle();
    get.mockResolvedValueOnce({ ...SPENT, failures: 4 });
    await settle(POLL_MS);

    expect(extracted).not.toHaveBeenCalled();
    expect(screen.getByText(/다시 추출하지 못했습니다/)).toBeTruthy();
    expect(button().disabled).toBe(false);
  });

  it("does not take a request still waiting for the worker as a run", async () => {
    await open(FINE);
    post.mockResolvedValue({ ...FINE, requested: true });

    fireEvent.click(button());
    await settle();
    get.mockResolvedValueOnce({ ...FINE, requested: true });
    await settle(POLL_MS);

    expect(button().disabled).toBe(true);
    expect(extracted).not.toHaveBeenCalled();
  });

  it("says a second press came too soon", async () => {
    await open(FINE);
    post.mockRejectedValue(new ApiError(429, "retry_too_soon", "asked a moment ago"));

    fireEvent.click(button());
    await settle();

    expect(screen.getByText(/방금 요청한 추출이 진행 중입니다/)).toBeTruthy();
    expect(button().disabled).toBe(false);
  });

  it("says there is nothing to extract from yet", async () => {
    await open(FINE);
    post.mockRejectedValue(new ApiError(409, "conflict", "no transcript"));

    fireEvent.click(button());
    await settle();

    expect(screen.getByText(/아직 전사된 내용이 없어/)).toBeTruthy();
  });

  it("shows nothing when the state could not be read", async () => {
    get.mockRejectedValueOnce(new Error("offline"));
    const { container } = render(<ReExtract meetingId="mtg_1" onExtracted={extracted} />);
    await settle();

    expect(container.textContent).toBe("");
  });
});
