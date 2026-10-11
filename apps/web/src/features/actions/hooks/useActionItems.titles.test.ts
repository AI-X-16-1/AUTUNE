import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useActionItems } from "./useActionItems";
import type { ActionItemRead } from "../types";

// The read for a run's titles (`titleReads`) is asked while a person may be
// working on the same rows: what it brings is the titles, and the list on the
// screen stays the screen's.

const list = vi.fn<() => Promise<ActionItemRead[]>>();
const patch = vi.fn<(id: string, changes: unknown) => Promise<ActionItemRead>>();
const destroy = vi.fn<(id: string) => Promise<void>>();
vi.mock("../api", () => ({
  listActionItems: () => list(),
  updateActionItem: (id: string, changes: unknown) => patch(id, changes),
  deleteActionItem: (id: string) => destroy(id),
  closeActionItem: vi.fn(),
  createActionItem: vi.fn(),
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
    status: "needs_confirmation",
    origin: "model",
    title: null,
    ...extra,
  }) as ActionItemRead;

const mounted = async (...items: ActionItemRead[]) => {
  list.mockResolvedValueOnce(items);
  const hook = renderHook(() => useActionItems({ meeting_id: "mtg_1" }));
  await waitFor(() => expect(hook.result.current.settled).toBe(true));
  return hook;
};

describe("useActionItems, the titles read", () => {
  it("takes the titles and keeps what a person changed since the list was asked for", async () => {
    const hook = await mounted(item("a"), item("b"));
    // Asked now; the server read both rows before the change below.
    let answer: (value: ActionItemRead[]) => void = () => undefined;
    list.mockReturnValueOnce(new Promise<ActionItemRead[]>((resolve) => (answer = resolve)));
    let read: Promise<void> = Promise.resolve();
    act(() => {
      read = hook.result.current.readTitles();
    });

    patch.mockResolvedValue(item("a", { status: "todo" }));
    await act(async () => {
      await hook.result.current.edit("a", { status: "todo" });
    });
    await act(async () => {
      answer([item("a", { title: "보고서 정리" }), item("b", { title: "서버 점검" })]);
      await read;
    });

    expect(hook.result.current.items).toEqual([
      item("a", { status: "todo", title: "보고서 정리" }),
      item("b", { title: "서버 점검" }),
    ]);
  });

  it("does not bring back a row deleted since the list was asked for", async () => {
    const hook = await mounted(item("a"), item("b"));
    destroy.mockResolvedValue(undefined);
    await act(async () => {
      await hook.result.current.remove("b");
    });

    list.mockResolvedValueOnce([item("a", { title: "보고서 정리" }), item("b", { title: "서버 점검" })]);
    await act(async () => {
      await hook.result.current.readTitles();
    });

    expect(hook.result.current.items).toEqual([item("a", { title: "보고서 정리" })]);
  });

  it("leaves the list and says no error when the read fails", async () => {
    const hook = await mounted(item("a"));

    list.mockRejectedValueOnce(new Error("offline"));
    await act(async () => {
      await hook.result.current.readTitles();
    });

    expect(hook.result.current.items).toEqual([item("a")]);
    expect(hook.result.current.error).toBeNull();
  });
});
