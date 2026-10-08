import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useActionItems } from "./useActionItems";
import type { ActionItemRead } from "../types";

// Closing an item without finishing it (#856): one POST, and the list then
// holds what the server answered -- the item in 완료, marked closed -- with no
// second read of the whole list.

const list = vi.fn<() => Promise<ActionItemRead[]>>();
const close = vi.fn<(id: string) => Promise<ActionItemRead>>();
const patch = vi.fn();
vi.mock("../api", () => ({
  listActionItems: () => list(),
  closeActionItem: (id: string) => close(id),
  updateActionItem: (...args: unknown[]) => patch(...args),
  createActionItem: vi.fn(),
  deleteActionItem: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const item = (id: string, extra: Partial<ActionItemRead> = {}) =>
  ({
    id,
    meeting_id: "mtg_1",
    description: `항목 ${id}`,
    status: "todo",
    is_candidate: false,
    ...extra,
  }) as ActionItemRead;

describe("useActionItems, close", () => {
  it("closes through its own call and keeps the server's answer for that item alone", async () => {
    list.mockResolvedValue([item("a"), item("b")]);
    close.mockResolvedValue(item("a", { status: "done", closed_unfinished: true }));
    const { result } = renderHook(() => useActionItems({ meeting_id: "mtg_1" }));
    await waitFor(() => expect(result.current.settled).toBe(true));

    await act(async () => {
      await result.current.close("a");
    });

    expect(close).toHaveBeenCalledWith("a");
    expect(patch).not.toHaveBeenCalled();
    expect(list).toHaveBeenCalledTimes(1);
    expect(
      result.current.items.map((i) => [i.id, i.status, i.closed_unfinished ?? false]),
    ).toEqual([
      ["a", "done", true],
      ["b", "todo", false],
    ]);
  });

  it("leaves the list as it was when the close is refused", async () => {
    list.mockResolvedValue([item("a")]);
    close.mockRejectedValue(new Error("409"));
    const { result } = renderHook(() => useActionItems({ meeting_id: "mtg_1" }));
    await waitFor(() => expect(result.current.settled).toBe(true));

    await act(async () => {
      await expect(result.current.close("a")).rejects.toThrow("409");
    });

    expect(result.current.items.map((i) => [i.id, i.status])).toEqual([["a", "todo"]]);
  });
});
