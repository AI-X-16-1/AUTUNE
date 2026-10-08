import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { MISS_LIMIT, POLL_MS, ReExtract } from "./ReExtract";
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
  not_published: false,
  requested: false,
  requested_at: null,
  in_progress: false,
  overdue: false,
  read_nothing: false,
};
const RETRYING: ExtractionState = {
  ...FINE,
  extracted_at: null,
  failures: 1,
  failed_at: "2026-10-05T14:24:41Z",
  will_retry: true,
};
const SPENT: ExtractionState = { ...RETRYING, failures: 3, will_retry: false };
// A transcript is stored and the first run has left nothing yet.
const RUNNING: ExtractionState = { ...FINE, extracted_at: null, in_progress: true };
const OVERDUE: ExtractionState = { ...RUNNING, in_progress: false, overdue: true };
const GOING = /추출하고 있습니다/;

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

  it("does not say 'could not extract' of a result that was stored and not passed on", async () => {
    // PARK, review of #868: the tab showed this run's rows under "추출하지 못해
    // 다시 시도 중", because a failed publish was counted as a failed extraction.
    await open({ ...RETRYING, extracted_at: "2026-10-06T01:00:00Z", not_published: true });

    const line = screen.getByRole("status").textContent;
    expect(line).toContain("아래 액션 아이템과 결정은 추출되었습니다");
    expect(line).toContain("전달하지 못해 자동으로 다시 시도하고 있습니다(1번 실패)");
    expect(line).not.toContain("추출하지 못해");
  });

  it("says the same of one that is out of tries, with the button", async () => {
    await open({ ...SPENT, extracted_at: "2026-10-06T01:00:00Z", not_published: true });

    const alert = screen.getByRole("alert").textContent;
    expect(alert).toContain("추출되었지만");
    expect(alert).toContain("전달하지");
    expect(alert).not.toContain("추출하지 못했습니다");
    expect(button().disabled).toBe(false);
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

  // dev, 2026-10-08: no items and no decisions minutes after a transcription,
  // and both there after "다시 추출". The first run was still going, and the
  // screen said nothing about it and never looked again.
  describe("a first run that is not in yet", () => {
    it("is said, looked for again, and has the board read when it is in", async () => {
      await open(RUNNING);
      expect(screen.getByText(GOING)).toBeTruthy();

      get.mockResolvedValueOnce(RUNNING);
      await settle(POLL_MS);
      expect(get).toHaveBeenCalledTimes(2);
      expect(screen.getByText(GOING)).toBeTruthy();
      expect(extracted).not.toHaveBeenCalled();

      get.mockResolvedValueOnce(FINE);
      await settle(POLL_MS);
      expect(extracted).toHaveBeenCalledOnce();
      expect(screen.queryByText(GOING)).toBeNull();

      // In: nothing left to look for.
      await settle(POLL_MS);
      await settle(POLL_MS);
      expect(get).toHaveBeenCalledTimes(3);
    });

    it("is not looked for on a meeting that is not in progress", async () => {
      await open(FINE);
      await settle(POLL_MS);
      await settle(POLL_MS);

      expect(get).toHaveBeenCalledOnce();
      expect(screen.queryByText(GOING)).toBeNull();
    });

    it("gives way to the failure line when the run failed, and the board is left alone", async () => {
      await open(RUNNING);

      get.mockResolvedValueOnce(RETRYING);
      await settle(POLL_MS);
      await settle(POLL_MS);

      expect(screen.queryByText(GOING)).toBeNull();
      expect(screen.getByRole("status").textContent).toContain("자동으로 다시 시도하고 있습니다");
      expect(extracted).not.toHaveBeenCalled();
      expect(get).toHaveBeenCalledTimes(2);
    });

    it("does not stay 'in progress' for a run that never came", async () => {
      await open(RUNNING);

      get.mockResolvedValueOnce(OVERDUE);
      await settle(POLL_MS);
      await settle(POLL_MS);

      expect(screen.queryByText(GOING)).toBeNull();
      expect(screen.getByRole("status").textContent).toContain("아직 추출되지 않았습니다");
      expect(button().disabled).toBe(false);
      expect(extracted).not.toHaveBeenCalled();
      expect(get).toHaveBeenCalledTimes(2);
    });

    it("is looked for through a read that failed, and not for ever", async () => {
      await open(RUNNING);

      get.mockRejectedValueOnce(new Error("offline"));
      await settle(POLL_MS);
      get.mockResolvedValueOnce(RUNNING);
      await settle(POLL_MS);
      expect(get).toHaveBeenCalledTimes(3);

      get.mockRejectedValue(new Error("offline"));
      for (let i = 0; i < MISS_LIMIT + 3; i += 1) await settle(POLL_MS);
      expect(get).toHaveBeenCalledTimes(3 + MISS_LIMIT);
    });

    it("leaves the looking to a requested run while one is out", async () => {
      await open(RUNNING);
      post.mockResolvedValue({ ...RUNNING, requested: true, requested_at: "2026-10-08T05:00:00Z" });

      fireEvent.click(button());
      await settle();
      expect(screen.queryByText(GOING)).toBeNull();
      expect(screen.getByText(/다시 추출을 요청했습니다/)).toBeTruthy();

      get.mockResolvedValueOnce({ ...FINE, requested_at: "2026-10-08T05:00:00Z" });
      await settle(POLL_MS);

      // One read at the interval, not the two a second watch would make.
      expect(get).toHaveBeenCalledTimes(2);
      expect(extracted).toHaveBeenCalledOnce();
    });
  });

  it("says a run read nothing because no consent is on record, of the meeting only", async () => {
    await open({ ...FINE, read_nothing: true });

    const line = screen.getByRole("status").textContent ?? "";
    expect(line).toContain("녹음 동의가 기록되지 않아 이 회의의 발화를 읽지 않았습니다");
    expect(line).toContain("자동으로 다시 추출합니다");
    await settle(POLL_MS);
    expect(get).toHaveBeenCalledOnce();
  });

  it("says the failure and not the consent line when both are on record", async () => {
    await open({ ...RETRYING, extracted_at: FINE.extracted_at, read_nothing: true });

    expect(screen.getByRole("status").textContent).toContain("자동으로 다시 시도하고 있습니다");
    expect(screen.queryByText(/녹음 동의/)).toBeNull();
  });

  it("hands each state it reads to its parent", async () => {
    const states: ExtractionState[] = [];
    get.mockResolvedValueOnce(RUNNING);
    render(
      <ReExtract meetingId="mtg_1" onExtracted={extracted} onState={(read) => states.push(read)} />,
    );
    await settle();
    get.mockResolvedValueOnce(FINE);
    await settle(POLL_MS);

    expect(states.map((read) => read.in_progress)).toEqual([true, false]);
  });
});
