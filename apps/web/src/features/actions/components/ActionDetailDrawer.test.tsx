import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionDetailDrawer } from "./ActionDetailDrawer";
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
    expect(screen.getAllByRole("button", { name: "삭제" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "닫기" })).toHaveLength(1);
  });

  it("only asks when 삭제 is pressed: nothing is deleted until the confirmation", () => {
    const { onDelete, onClose, header } = open();

    fireEvent.click(within(header).getByRole("button", { name: "삭제" }));

    expect(onDelete).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    // The confirmation is on screen: more than the header's one 삭제 now.
    expect(screen.getAllByRole("button").length).toBeGreaterThan(2);
  });

  it("closes with 닫기 and deletes nothing", () => {
    const { onDelete, onClose, header } = open();

    fireEvent.click(within(header).getByRole("button", { name: "닫기" }));

    expect(onClose).toHaveBeenCalledOnce();
    expect(onDelete).not.toHaveBeenCalled();
  });
});
