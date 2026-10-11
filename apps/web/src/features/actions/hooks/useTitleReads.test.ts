import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTitleReads } from "./useTitleReads";

// When the tab asks for titles after a run: at ten, thirty and sixty seconds,
// while a row has none, and never on a page that saw no run end.

const read = vi.fn();
const mount = (run: number, waiting: boolean) =>
  renderHook(({ run, waiting }) => useTitleReads(run, waiting, read), {
    initialProps: { run, waiting },
  });

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  read.mockClear();
});

describe("useTitleReads", () => {
  it("asks nothing on a screen that has seen no run end", () => {
    mount(0, true);

    vi.advanceTimersByTime(120_000);

    expect(read).not.toHaveBeenCalled();
  });

  it("asks at 10, 30 and 60 seconds after a run while a row has no title", () => {
    const hook = mount(0, true);
    hook.rerender({ run: 1, waiting: true });

    vi.advanceTimersByTime(9_999);
    expect(read).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(read).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(20_000);
    expect(read).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(30_000);
    expect(read).toHaveBeenCalledTimes(3);

    vi.advanceTimersByTime(600_000);
    expect(read).toHaveBeenCalledTimes(3);
  });

  it("stops asking once every row has its title", () => {
    const hook = mount(1, true);

    vi.advanceTimersByTime(10_000);
    expect(read).toHaveBeenCalledTimes(1);
    hook.rerender({ run: 1, waiting: false });
    vi.advanceTimersByTime(600_000);

    expect(read).toHaveBeenCalledTimes(1);
  });

  it("looks at the rows as they are at each moment, not as they were when the run ended", () => {
    // The run's own rows are read a moment after it ends: until then the
    // screen holds the rows from before, which may all be titled.
    const hook = mount(1, false);
    hook.rerender({ run: 1, waiting: true });

    vi.advanceTimersByTime(10_000);

    expect(read).toHaveBeenCalledTimes(1);
  });

  it("starts over when another run ends", () => {
    const hook = mount(1, true);
    vi.advanceTimersByTime(25_000);
    expect(read).toHaveBeenCalledTimes(1);

    hook.rerender({ run: 2, waiting: true });
    vi.advanceTimersByTime(9_999);
    // The first run's 30-second read is not asked for the second run's rows.
    expect(read).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("asks nothing after the screen is left", () => {
    const hook = mount(1, true);

    hook.unmount();
    vi.advanceTimersByTime(120_000);

    expect(read).not.toHaveBeenCalled();
  });

  it("calls the read the screen has now", () => {
    const later = vi.fn();
    const hook = renderHook(({ fn }) => useTitleReads(1, true, fn), { initialProps: { fn: read } });
    hook.rerender({ fn: later });

    vi.advanceTimersByTime(10_000);

    expect(read).not.toHaveBeenCalled();
    expect(later).toHaveBeenCalledOnce();
  });
});
