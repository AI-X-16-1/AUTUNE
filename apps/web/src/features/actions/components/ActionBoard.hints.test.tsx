import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActionBoard } from "./ActionBoard";
import type { ActionItemRead, ActionStatus } from "../types";

// What the board says without being asked (#1183): that a card can be dragged,
// that the bulk actions wait for a tick, and nothing about "0건" of evidence.

beforeEach(() => window.localStorage.clear());
afterEach(cleanup);

function item(id: string, status: ActionStatus, extra: Partial<ActionItemRead> = {}) {
  return {
    id,
    meeting_id: "mtg_1",
    description: `항목 ${id}`,
    status,
    is_candidate: false,
    source_utterance_ids: ["u1"],
    deleted_source_count: 0,
    ...extra,
  } as ActionItemRead;
}

describe("ActionBoard, the line about dragging", () => {
  it("says a card can be dragged, until it is dismissed, and then no more", async () => {
    const { unmount } = render(
      <ActionBoard items={[item("a", "todo")]} onMove={vi.fn(() => Promise.resolve())} />,
    );

    const hint = await screen.findByText(/카드를 끌어 다른 열로 옮길 수 있습니다/);
    expect(hint).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "알겠습니다" }));
    expect(screen.queryByText(/카드를 끌어/)).toBeNull();
    unmount();

    // A later visit: remembered.
    render(<ActionBoard items={[item("a", "todo")]} onMove={vi.fn(() => Promise.resolve())} />);
    await waitFor(() => expect(window.localStorage.getItem("autune.board.dragHintSeen")).toBe("1"));
    expect(screen.queryByText(/카드를 끌어/)).toBeNull();
  });

  it("is not said where nothing can be moved, or on an empty board", async () => {
    render(<ActionBoard items={[item("a", "todo")]} />);
    cleanup();
    render(<ActionBoard items={[]} onMove={vi.fn()} />);

    await Promise.resolve();

    expect(screen.queryByText(/카드를 끌어/)).toBeNull();
  });
});

describe("ActionBoard, the bulk bar", () => {
  const drafts = [item("a", "needs_confirmation"), item("b", "needs_confirmation")];

  it("offers 모두 선택 alone until something is ticked", () => {
    render(<ActionBoard items={drafts} onBulk={vi.fn()} />);

    expect(screen.getByLabelText("모두 선택")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /선택 확정/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "선택 삭제" })).toBeNull();
  });

  it("shows the actions once one is ticked, and hides them when it is unticked", () => {
    render(<ActionBoard items={drafts} onBulk={vi.fn()} />);

    fireEvent.click(screen.getByLabelText("항목 a 선택"));
    expect(screen.getByRole("button", { name: "선택 확정 (1)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "선택 삭제" })).toBeTruthy();

    fireEvent.click(screen.getByLabelText("항목 a 선택"));
    expect(screen.queryByRole("button", { name: /선택 확정/ })).toBeNull();
  });
});

describe("ActionBoard, the line under a card's title", () => {
  it("does not say 근거 발화 0건 for a card that has no sources", () => {
    render(
      <ActionBoard
        items={[item("a", "todo", { source_utterance_ids: [], summary: null })]}
      />,
    );

    expect(screen.queryByText(/근거 발화 0건/)).toBeNull();
  });

  it("still counts the evidence when there is some, and says when it was deleted", () => {
    render(
      <ActionBoard
        items={[
          item("a", "todo", { source_utterance_ids: ["u1", "u2"], summary: null }),
          item("b", "todo", { source_utterance_ids: [], deleted_source_count: 2, summary: null }),
        ]}
      />,
    );

    expect(screen.getByText("근거 발화 2건")).toBeTruthy();
    expect(screen.getByText("근거 발화 삭제됨")).toBeTruthy();
  });

  it("lets a candidate with nothing to cite still say it is one", () => {
    render(
      <ActionBoard
        items={[
          item("a", "needs_confirmation", {
            is_candidate: true,
            source_utterance_ids: [],
            summary: null,
          }),
        ]}
      />,
    );

    expect(screen.getByText("후보", { selector: "div" })).toBeTruthy();
  });
});
