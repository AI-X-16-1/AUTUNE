import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActionDetailDrawer } from "./ActionDetailDrawer";
import type { ActionItemRead, ActionStatus } from "../types";

// "끝내지 않고 닫기" in the detail window (#856; the user, 2026-10-09): under
// the status, on an open item only, and closed at once -- no confirmation,
// because the status select takes it back.

vi.mock("../hooks/useSourceUtterances", () => ({
  useSourceUtterances: () => ({
    sources: [],
    context: [],
    related: [],
    loading: false,
    error: null,
    dmUrl: null,
    history: [],
  }),
}));
vi.mock("../hooks/useAssignable", () => ({ useAssignable: () => [] }));

const LABEL = "끝내지 않고 닫기";
const NOTICE = "끝내지 않고 닫았습니다. 상태를 다시 바꾸면 되돌릴 수 있습니다.";
const FAILED = "닫지 못했습니다. 잠시 후 다시 시도해 주세요.";

function item(status: ActionStatus, extra: Partial<ActionItemRead> = {}) {
  return {
    id: "act_1",
    meeting_id: "mtg_1",
    description: "접을 일",
    status,
    confidence: 0.92,
    is_candidate: false,
    ...extra,
  } as ActionItemRead;
}

const control = () => screen.queryByRole("button", { name: LABEL });

let onCloseUnfinished: ReturnType<typeof vi.fn<() => Promise<void>>>;

beforeEach(() => {
  onCloseUnfinished = vi.fn<() => Promise<void>>(async () => undefined);
});

afterEach(cleanup);

function show(status: ActionStatus, extra: Partial<ActionItemRead> = {}) {
  return render(
    <ActionDetailDrawer
      item={item(status, extra)}
      onClose={vi.fn()}
      onCloseUnfinished={onCloseUnfinished}
    />,
  );
}

describe("끝내지 않고 닫기 in the detail window", () => {
  it.each(["todo", "in_progress"] as const)("is offered for an item in %s", (status) => {
    show(status);

    expect(control()).not.toBeNull();
  });

  it("is not offered for an item still waiting for confirmation", () => {
    show("needs_confirmation");

    expect(control()).toBeNull();
  });

  it.each([false, true])(
    "is not offered for an item in 완료 (closed before: %s)",
    (closed_unfinished) => {
      show("done", { closed_unfinished });

      expect(control()).toBeNull();
    },
  );

  it("is not offered where nothing can close the item", () => {
    render(<ActionDetailDrawer item={item("todo")} onClose={vi.fn()} />);

    expect(control()).toBeNull();
  });

  it("is not the window's own 닫기: the window stays open after it", async () => {
    const onClose = vi.fn();
    render(
      <ActionDetailDrawer
        item={item("todo")}
        onClose={onClose}
        onCloseUnfinished={onCloseUnfinished}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: LABEL }));

    // Once the close has come back, not just while it is on its way.
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe(NOTICE));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "닫기" })).toBeTruthy();
  });

  it("closes at once, asks nothing first, and then says what happened", async () => {
    show("todo");

    fireEvent.click(screen.getByRole("button", { name: LABEL }));

    expect(onCloseUnfinished).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getAllByRole("dialog")).toHaveLength(1); // the window itself
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe(NOTICE));
    expect(screen.queryByText(FAILED)).toBeNull();
  });

  it("cannot be pressed twice while the first is on its way", async () => {
    let finish: () => void = () => undefined;
    onCloseUnfinished.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    show("in_progress");

    fireEvent.click(screen.getByRole("button", { name: LABEL }));
    await waitFor(() =>
      expect((control() as HTMLButtonElement).disabled).toBe(true),
    );
    fireEvent.click(screen.getByRole("button", { name: LABEL }));

    expect(onCloseUnfinished).toHaveBeenCalledTimes(1);
    finish();
    await waitFor(() =>
      expect((control() as HTMLButtonElement).disabled).toBe(false),
    );
  });

  it("says so when the close did not go through, and claims nothing", async () => {
    onCloseUnfinished.mockRejectedValue(new Error("409"));
    show("todo");

    fireEvent.click(screen.getByRole("button", { name: LABEL }));

    await waitFor(() => expect(screen.getByText(FAILED)).toBeTruthy());
    expect(screen.queryByText(NOTICE)).toBeNull();
  });
});
