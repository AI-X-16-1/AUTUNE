import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionBoard } from "./ActionBoard";
import type { BulkActionResult } from "../api";
import type { ActionItemRead, ActionStatus } from "../types";

// 확인 필요 worked through several at a time (the user, 2026-10-04): tick,
// then confirm or delete the ticked ones; a deletion asks once.

afterEach(cleanup);

function item(id: string, status: ActionStatus): ActionItemRead {
  return {
    id,
    meeting_id: "mtg_1",
    description: `항목 ${id}`,
    status,
    is_candidate: false,
  } as ActionItemRead;
}

const ITEMS = [
  item("a", "needs_confirmation"),
  item("b", "needs_confirmation"),
  item("c", "todo"),
];

describe("ActionBoard, several drafts at once", () => {
  it("confirms the ticked drafts in one request and says how many", async () => {
    const onBulk = vi.fn<
      (ids: string[], action: "confirm" | "delete") => Promise<BulkActionResult>
    >(() => Promise.resolve({ confirmed: ["a"], deleted: [], skipped: [] }));
    render(<ActionBoard items={ITEMS} onBulk={onBulk} />);

    fireEvent.click(screen.getByLabelText("항목 a 선택"));
    fireEvent.click(screen.getByRole("button", { name: "선택 확정 (1)" }));

    expect(await screen.findByText(/1개를 확정했습니다/)).toBeTruthy();
    expect(onBulk).toHaveBeenCalledWith(["a"], "confirm");
  });

  it("selects every draft, and only drafts, with 모두 선택", () => {
    render(<ActionBoard items={ITEMS} onBulk={vi.fn()} />);

    fireEvent.click(screen.getByLabelText("모두 선택"));

    expect(screen.getByRole("button", { name: "선택 확정 (2)" })).toBeTruthy();
    expect(screen.queryByLabelText("항목 c 선택")).toBeNull();
  });

  it("asks before deleting the ticked drafts", async () => {
    const onBulk = vi.fn<
      (ids: string[], action: "confirm" | "delete") => Promise<BulkActionResult>
    >(() =>
      Promise.resolve({ confirmed: [], deleted: ["a", "b"], skipped: [] }),
    );
    render(<ActionBoard items={ITEMS} onBulk={onBulk} />);

    fireEvent.click(screen.getByLabelText("모두 선택"));
    fireEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
    expect(onBulk).not.toHaveBeenCalled();
    expect(screen.getByText("2개를 삭제할까요?")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "삭제" }));

    expect(await screen.findByText(/2개를 삭제했습니다/)).toBeTruthy();
    expect(onBulk).toHaveBeenCalledWith(["a", "b"], "delete");
  });

  it("has no checkboxes when the board was given nothing to do them with", () => {
    render(<ActionBoard items={ITEMS} />);

    expect(screen.queryByLabelText("항목 a 선택")).toBeNull();
    expect(screen.queryByLabelText("모두 선택")).toBeNull();
  });
});
