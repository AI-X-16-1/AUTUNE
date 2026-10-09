import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { LiveRow } from "../types";
import { useLiveResearch } from "./useLiveResearch";

function row(i: number): LiveRow {
  return {
    utterance: {
      id: `utt_live_${i}`,
      speaker: "김민경",
      speaker_id: null,
      role: null,
      start: i,
      end: i + 1,
      text: `말 ${i}`,
      confidence: 1,
    },
  } as LiveRow;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(api, "listLiveResearch").mockResolvedValue([]);
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useLiveResearch", () => {
  it("sends six new rows without their speaker", async () => {
    const detect = vi.spyOn(api, "detectLive").mockResolvedValue({ queued: true });
    const { rerender } = renderHook(({ rows }) => useLiveResearch("mtg_1", rows, true), {
      initialProps: { rows: [0, 1, 2, 3, 4].map(row) },
    });
    expect(detect).not.toHaveBeenCalled();

    rerender({ rows: [0, 1, 2, 3, 4, 5].map(row) });
    await act(async () => {});

    expect(detect).toHaveBeenCalledTimes(1);
    const sent = detect.mock.calls[0]?.[1] ?? [];
    expect(sent).toHaveLength(6);
    expect(sent[0]).toEqual({ start: 0, text: "말 0" });
    expect(JSON.stringify(sent)).not.toContain("김민경");
  });

  it("sends what is new after 45 seconds even when fewer than six", async () => {
    const detect = vi.spyOn(api, "detectLive").mockResolvedValue({ queued: true });
    renderHook(() => useLiveResearch("mtg_1", [0, 1].map(row), true));

    await act(async () => {
      vi.advanceTimersByTime(45_000);
    });

    expect(detect).toHaveBeenCalledTimes(1);
    expect(detect.mock.calls[0]?.[1]).toHaveLength(2);
  });

  it("stops sending once the meeting has its five", async () => {
    const detect = vi
      .spyOn(api, "detectLive")
      .mockRejectedValue(Object.assign(new Error("cap"), { status: 429 }));
    const { rerender } = renderHook(({ rows }) => useLiveResearch("mtg_1", rows, true), {
      initialProps: { rows: [0, 1, 2, 3, 4, 5].map(row) },
    });
    await act(async () => {});

    rerender({ rows: Array.from({ length: 12 }, (_, i) => row(i)) });
    await act(async () => {});

    expect(detect).toHaveBeenCalledTimes(1);
  });

  it("asks about one row with up to four rows before it", async () => {
    const ask = vi.spyOn(api, "researchLive").mockResolvedValue({ id: "alr_1" });
    const { result } = renderHook(() =>
      useLiveResearch("mtg_1", Array.from({ length: 8 }, (_, i) => row(i)), false),
    );

    await act(async () => {
      await result.current.research(6);
    });

    expect(ask).toHaveBeenCalledWith(
      "mtg_1",
      { start: 6, text: "말 6" },
      [2, 3, 4, 5].map((i) => ({ start: i, text: `말 ${i}` })),
    );
  });
});
