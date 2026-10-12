import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionDetailDrawer } from "./ActionDetailDrawer";
import { CONFIRMED_NOTICE } from "../board";
import type { ActionItemRead } from "../types";

// The window's own two controls (S18): 삭제 then 닫기, together in the header
// (the user, 2026-10-02). What matters is the order, that 삭제 still only asks,
// and that nothing was left behind at the bottom.

// The quotation is fetched when the window opens; no network here.
vi.mock("../hooks/useSourceUtterances", () => ({
  useSourceUtterances: () => ({ status: "loading" }),
}));

afterEach(cleanup);

const ITEM = {
  id: "a",
  meeting_id: "mtg_1",
  description: "배포 일정 공유",
  status: "todo",
  confidence: 0.92,
  is_candidate: false,
} as ActionItemRead;

function open(onClose = vi.fn(), onDelete = vi.fn(() => Promise.resolve())) {
  render(<ActionDetailDrawer item={ITEM} onClose={onClose} onDelete={onDelete} />);
  const header = screen.getByRole("banner");
  return { onClose, onDelete, header };
}

describe("ActionDetailDrawer, the window's own controls", () => {
  it("puts 삭제 and then 닫기 side by side in the header", () => {
    const { header } = open();

    const buttons = within(header).getAllByRole("button");

    expect(buttons.map((button) => button.textContent)).toEqual(["삭제", "닫기"]);
  });

  it("leaves no second set of controls at the bottom of the window", () => {
    open();

    expect(screen.queryByRole("contentinfo")).toBeNull();
    expect(screen.getAllByRole("button", { name: "항목 삭제" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "닫기" })).toHaveLength(1);
  });

  it("only asks when 삭제 is pressed: nothing is deleted until the confirmation", () => {
    const { onDelete, onClose, header } = open();

    fireEvent.click(within(header).getByRole("button", { name: "항목 삭제" }));

    expect(onDelete).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    // The confirmation is on screen: more than the header's one 삭제 now.
    expect(screen.getAllByRole("button").length).toBeGreaterThan(2);
  });

  it("names the delete button for what it deletes", () => {
    const { header } = open();

    const button = within(header).getByRole("button", { name: "항목 삭제" });

    expect(button.textContent).toBe("삭제");
  });

  it("closes with 닫기 and deletes nothing", () => {
    const { onDelete, onClose, header } = open();

    fireEvent.click(within(header).getByRole("button", { name: "닫기" }));

    expect(onClose).toHaveBeenCalledOnce();
    expect(onDelete).not.toHaveBeenCalled();
  });
});

describe("ActionDetailDrawer, a status change that confirms", () => {
  // A drop on the board says so after it confirmed an item (#712); the same
  // change made with this window's select said nothing (review of #717).
  const UNCONFIRMED = { ...ITEM, status: "needs_confirmation" } as ActionItemRead;

  function change(item: ActionItemRead, to: string, onStatusChange = vi.fn(() => Promise.resolve())) {
    render(<ActionDetailDrawer item={item} onClose={vi.fn()} onStatusChange={onStatusChange} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: to } });
    return onStatusChange;
  }

  it("says the item was confirmed, in the board's own words", async () => {
    const onStatusChange = change(UNCONFIRMED, "todo");

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toBe(CONFIRMED_NOTICE),
    );
    expect(onStatusChange).toHaveBeenCalledExactlyOnceWith("todo");
  });

  it("says nothing after a change between the other statuses", async () => {
    const onStatusChange = change(ITEM, "done");

    await waitFor(() => expect(onStatusChange).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByRole("combobox").getAttribute("aria-busy")).toBeNull());
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("does not say an item was confirmed when the change was refused", async () => {
    change(UNCONFIRMED, "todo", vi.fn(() => Promise.reject(new Error("refused"))));

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("ActionDetailDrawer, an item closed without being finished", () => {
  // It sits in 완료 on the board (#856); the window's header must not call it
  // 완료 while the card says 닫힘 and the history says 끝내지 않고 닫힘.
  it("says it was closed without being finished, and a finished one says 완료", () => {
    render(
      <ActionDetailDrawer
        item={{ ...ITEM, status: "done", closed_unfinished: true } as ActionItemRead}
        onClose={vi.fn()}
      />,
    );
    const header = screen.getByRole("banner");
    expect(within(header).getByText("끝내지 않고 닫힘")).toBeTruthy();
    expect(within(header).queryByText("완료")).toBeNull();
    cleanup();

    render(<ActionDetailDrawer item={{ ...ITEM, status: "done" } as ActionItemRead} onClose={vi.fn()} />);
    expect(within(screen.getByRole("banner")).getByText("완료")).toBeTruthy();
  });
});
