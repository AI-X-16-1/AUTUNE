import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useCarriedAgenda } from "./useCarriedAgenda";
import type { CarriedGroup, CarriedLine, CarriedSource } from "./useCarriedAgenda";
import type { BriefRead } from "../types";

// What the hook hands back on every render, not only on the one the screen
// settles on: the render in which the brief changes comes before the effect
// that empties what was held for the brief before it.

afterEach(cleanup);

const brief = (meetingId: string, earlier: string): BriefRead => ({
  meeting_id: meetingId,
  title: "주간 회의",
  starts_at: null,
  recap: { meeting_id: earlier, title: "지난 주간 회의", day: "2026-10-05", topics: [], decisions: [] },
  recap_gone: false,
  match_reason: "series",
  agenda: [],
  sent_at: null,
});

describe("useCarriedAgenda", () => {
  it("hands back nothing of the earlier brief in the very render the brief changes", async () => {
    // Answers at once for the first brief, and never for the second.
    const lines = vi.fn<CarriedSource["lines"]>((earlier) =>
      earlier === "old_a" ? Promise.resolve([{ title: "A 회의의 갭" }]) : new Promise(() => undefined),
    );
    const sources = [{ label: "지난 회의의 미해결 갭", lines }];
    const renders: { earlier: string; groups: readonly CarriedGroup[] }[] = [];
    const view = renderHook(
      ({ current }) => {
        const { groups } = useCarriedAgenda(current, sources);
        renders.push({ earlier: current.recap?.meeting_id ?? "", groups });
        return groups;
      },
      { initialProps: { current: brief("mtg_a", "old_a") } },
    );
    await waitFor(() => expect(view.result.current).toHaveLength(1));

    view.rerender({ current: brief("mtg_b", "old_b") });
    await waitFor(() => expect(lines).toHaveBeenCalledWith("old_b", expect.anything()));

    const underTheNewBrief = renders.filter((render) => render.earlier === "old_b");
    expect(underTheNewBrief.length).toBeGreaterThan(0);
    expect(underTheNewBrief.map((render) => render.groups)).toEqual(underTheNewBrief.map(() => []));
  });
});

// "Nothing" and "not known yet" are both an empty list; `waiting` tells them
// apart, on every render -- the first one included, before any effect ran.
describe("useCarriedAgenda, while a source is still out", () => {
  /** A source the test answers, or fails, when it chooses. */
  function held() {
    let answer: (lines: readonly CarriedLine[]) => void = () => undefined;
    let fail: (error: Error) => void = () => undefined;
    const lines = vi.fn<CarriedSource["lines"]>(
      () =>
        new Promise<readonly CarriedLine[]>((resolve, reject) => {
          answer = resolve;
          fail = reject;
        }),
    );
    return {
      source: { label: "지난 회의의 미해결 갭", lines },
      answer: (given: readonly CarriedLine[]) => answer(given),
      fail: () => fail(new Error("boom")),
    };
  }
  const waitingOn = (sources: readonly CarriedSource[] | undefined, current: BriefRead | null) => {
    const seen: boolean[] = [];
    const view = renderHook(() => {
      const carried = useCarriedAgenda(current, sources);
      seen.push(carried.waiting);
      return carried;
    });
    return { view, seen };
  };

  it("is waiting from the first render, and until the last source has answered", async () => {
    const first = held();
    const second = held();
    const { view, seen } = waitingOn([first.source, second.source], brief("mtg_a", "old_a"));

    expect(seen[0]).toBe(true);

    first.answer([]);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(view.result.current.waiting).toBe(true);

    second.answer([{ title: "A 회의의 갭" }]);
    await waitFor(() => expect(view.result.current.waiting).toBe(false));
    expect(view.result.current.groups).toHaveLength(1);
  });

  it("stops waiting when every source answered with nothing", async () => {
    const only = held();
    const { view } = waitingOn([only.source], brief("mtg_a", "old_a"));

    only.answer([]);

    await waitFor(() => expect(view.result.current.waiting).toBe(false));
    expect(view.result.current.groups).toEqual([]);
  });

  it("counts a source that cannot be read as answered, with nothing", async () => {
    const only = held();
    const { view } = waitingOn([only.source], brief("mtg_a", "old_a"));
    expect(view.result.current.waiting).toBe(true);

    only.fail();

    await waitFor(() => expect(view.result.current.waiting).toBe(false));
    expect(view.result.current.groups).toEqual([]);
  });

  it("waits for nothing when nothing is asked: no brief, no earlier meeting, no source", () => {
    const only = held();
    const noEarlier: BriefRead = { ...brief("mtg_a", "old_a"), recap: null };

    expect(waitingOn([only.source], null).seen).toEqual([false]);
    expect(waitingOn([only.source], noEarlier).seen).toEqual([false]);
    expect(waitingOn(undefined, brief("mtg_a", "old_a")).seen).toEqual([false]);
    expect(waitingOn([], brief("mtg_a", "old_a")).seen.at(-1)).toBe(false);
    expect(only.source.lines).not.toHaveBeenCalled();
  });

  it("is waiting again in the very render the brief changes", async () => {
    const lines = vi.fn<CarriedSource["lines"]>((earlier) =>
      earlier === "old_a" ? Promise.resolve([]) : new Promise(() => undefined),
    );
    const sources = [{ label: "지난 회의의 미해결 갭", lines }];
    const renders: { earlier: string; waiting: boolean }[] = [];
    const view = renderHook(
      ({ current }) => {
        const carried = useCarriedAgenda(current, sources);
        renders.push({ earlier: current.recap?.meeting_id ?? "", waiting: carried.waiting });
        return carried;
      },
      { initialProps: { current: brief("mtg_a", "old_a") } },
    );
    await waitFor(() => expect(view.result.current.waiting).toBe(false));

    view.rerender({ current: brief("mtg_b", "old_b") });
    await waitFor(() => expect(lines).toHaveBeenCalledWith("old_b", expect.anything()));

    const underTheNewBrief = renders.filter((render) => render.earlier === "old_b");
    expect(underTheNewBrief.length).toBeGreaterThan(0);
    expect(underTheNewBrief.every((render) => render.waiting)).toBe(true);
  });
});

// A source is also told which meeting the brief is for and its title, both
// already on the brief: the team's uploaded materials are searched by that
// title (#1147 B2). Asked again when either changes, never under the old one.
describe("useCarriedAgenda, what a source is told about the brief", () => {
  it("hands every source the brief's meeting id and title beside the earlier meeting", async () => {
    const lines = vi.fn<CarriedSource["lines"]>(() => Promise.resolve([]));
    // One list for every render, as the route's module-level list is.
    const sources = [{ label: "올린 자료", lines }];
    const current = brief("mtg_a", "old_a");
    renderHook(() => useCarriedAgenda(current, sources));

    await waitFor(() =>
      expect(lines).toHaveBeenCalledWith("old_a", { meetingId: "mtg_a", title: "주간 회의" }),
    );
  });

  it("asks again when only the brief's title changes, and drops what the old title answered", async () => {
    const lines = vi.fn<CarriedSource["lines"]>((_earlier, about) =>
      about.title === "주간 회의"
        ? Promise.resolve([{ title: "출시 계획" }])
        : new Promise(() => undefined),
    );
    const sources = [{ label: "올린 자료", lines }];
    const view = renderHook(({ current }) => useCarriedAgenda(current, sources), {
      initialProps: { current: brief("mtg_a", "old_a") },
    });
    await waitFor(() => expect(view.result.current.groups).toHaveLength(1));

    view.rerender({ current: { ...brief("mtg_a", "old_a"), title: "출시 점검" } });

    expect(view.result.current.groups).toEqual([]);
    await waitFor(() =>
      expect(lines).toHaveBeenCalledWith("old_a", { meetingId: "mtg_a", title: "출시 점검" }),
    );
    expect(view.result.current.waiting).toBe(true);
  });
});
