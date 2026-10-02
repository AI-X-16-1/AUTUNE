import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionBoard } from "./ActionBoard";
import type { ActionItemRead, ActionStatus } from "../types";

// Dragging a card to another column (S17). A drop is the same change the detail
// window's select makes, so what matters here is what is sent, where the card
// is drawn while it is sent and after a refusal, and what a drag may not do:
// move a candidate, or carry the item's text out of the page.

afterEach(cleanup);

function item(id: string, status: ActionStatus, extra: Partial<ActionItemRead> = {}) {
  return {
    id,
    meeting_id: "mtg_1",
    description: `항목 ${id}`,
    status,
    is_candidate: false,
    ...extra,
  } as ActionItemRead;
}

const ITEMS = [item("a", "todo"), item("b", "needs_confirmation"), item("c", "done")];

const card = (id: string) => screen.getByText(`항목 ${id}`).closest("[role='button']") as HTMLElement;
const column = (label: string) => screen.getByRole("region", { name: label });

/** What the browser hands a drag handler; jsdom has no DataTransfer of its own. */
function transfer() {
  return { setData: vi.fn(), effectAllowed: "", dropEffect: "" };
}

function drag(id: string, to: string) {
  const dataTransfer = transfer();
  fireEvent.dragStart(card(id), { dataTransfer });
  fireEvent.dragEnter(column(to), { dataTransfer });
  fireEvent.dragOver(column(to), { dataTransfer });
  fireEvent.drop(column(to), { dataTransfer });
  return dataTransfer;
}

describe("ActionBoard, dragging a card", () => {
  it("sends the column it was dropped on and draws the card there while it is sent", async () => {
    let settle: () => void = () => undefined;
    const onMove = vi.fn(() => new Promise<void>((resolve) => (settle = resolve)));
    render(<ActionBoard items={ITEMS} onMove={onMove} />);

    drag("a", "진행 중");

    expect(onMove).toHaveBeenCalledExactlyOnceWith("a", "in_progress");
    await waitFor(() => expect(within(column("진행 중")).getByText("항목 a")).toBeTruthy());
    // In flight it cannot be picked up again: a second drop would race the first.
    expect(card("a").getAttribute("draggable")).toBe("false");
    expect(card("a").getAttribute("aria-busy")).toBe("true");

    settle();
    await waitFor(() => expect(card("a").getAttribute("aria-busy")).toBeNull());
  });

  it("confirms an item dropped out of 확인 필요, at once, as the detail window's select does", () => {
    const onMove = vi.fn(() => Promise.resolve());
    render(<ActionBoard items={ITEMS} onMove={onMove} />);

    drag("b", "진행 전");

    expect(onMove).toHaveBeenCalledExactlyOnceWith("b", "todo");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("puts the card back and says so when the server refuses", async () => {
    const onMove = vi.fn(() => Promise.reject(new Error("refused")));
    render(<ActionBoard items={ITEMS} onMove={onMove} />);

    drag("a", "완료");

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("상태를 바꾸지 못했습니다"),
    );
    expect(within(column("진행 전")).getByText("항목 a")).toBeTruthy();
    expect(within(column("완료")).queryByText("항목 a")).toBeNull();
    expect(card("a").getAttribute("draggable")).toBe("true");
  });

  it("does not take a drop on the card's own column", () => {
    const onMove = vi.fn(() => Promise.resolve());
    render(<ActionBoard items={ITEMS} onMove={onMove} />);

    const dataTransfer = transfer();
    fireEvent.dragStart(card("a"), { dataTransfer });
    // Not prevented: the browser shows "not allowed" and never fires a drop.
    expect(fireEvent.dragOver(column("진행 전"), { dataTransfer })).toBe(true);
    expect(fireEvent.dragOver(column("완료"), { dataTransfer })).toBe(false);
    fireEvent.drop(column("진행 전"), { dataTransfer });

    expect(onMove).not.toHaveBeenCalled();
  });

  it("carries the item's id under Autune's own type and none of its text", () => {
    render(<ActionBoard items={ITEMS} onMove={() => Promise.resolve()} />);

    const dataTransfer = transfer();
    fireEvent.dragStart(card("a"), { dataTransfer });

    // text/plain would paste the item's wording into whatever it is dropped on.
    expect(dataTransfer.setData.mock.calls).toEqual([["application/x-autune-action-item", "a"]]);
  });

  it("leaves a candidate where it is: the band is a question, not a status", () => {
    const candidate = item("d", "needs_confirmation", { is_candidate: true });
    const onMove = vi.fn(() => Promise.resolve());
    render(<ActionBoard items={[...ITEMS, candidate]} onMove={onMove} />);

    expect(card("d").getAttribute("draggable")).toBe("false");
    drag("d", "진행 전");

    expect(onMove).not.toHaveBeenCalled();
  });

  it("is not draggable at all on a board that was given no way to move a card", () => {
    render(<ActionBoard items={ITEMS} />);

    expect(ITEMS.map(({ id }) => card(id).getAttribute("draggable"))).toEqual([
      "false",
      "false",
      "false",
    ]);
  });

  it("still opens a card with a click, Enter and Space", () => {
    const onSelect = vi.fn();
    render(<ActionBoard items={ITEMS} onSelect={onSelect} onMove={() => Promise.resolve()} />);

    fireEvent.click(card("a"));
    fireEvent.keyDown(card("a"), { key: "Enter" });
    fireEvent.keyDown(card("a"), { key: " " });
    fireEvent.keyDown(card("a"), { key: "Tab" });

    expect(onSelect.mock.calls).toEqual([["a"], ["a"], ["a"]]);
  });
});
