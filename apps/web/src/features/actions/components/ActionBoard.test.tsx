import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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

/** The board learns of a drag a tick after `dragstart`; let that tick pass. */
const tick = () => act(() => new Promise<void>((resolve) => setTimeout(resolve, 0)));

async function drag(id: string, to: string) {
  const dataTransfer = transfer();
  fireEvent.dragStart(card(id), { dataTransfer });
  await tick();
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

    await drag("a", "진행 중");

    expect(onMove).toHaveBeenCalledExactlyOnceWith("a", "in_progress");
    await waitFor(() => expect(within(column("진행 중")).getByText("항목 a")).toBeTruthy());
    // In flight it cannot be picked up again: a second drop would race the first.
    expect(card("a").getAttribute("draggable")).toBe("false");
    expect(card("a").getAttribute("aria-busy")).toBe("true");

    settle();
    await waitFor(() => expect(card("a").getAttribute("aria-busy")).toBeNull());
  });

  it("confirms an item dropped out of 확인 필요, at once, as the detail window's select does", async () => {
    const onMove = vi.fn(() => Promise.resolve());
    render(<ActionBoard items={ITEMS} onMove={onMove} />);

    await drag("b", "진행 전");

    expect(onMove).toHaveBeenCalledExactlyOnceWith("b", "todo");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("puts the card back and says so when the server refuses", async () => {
    const onMove = vi.fn(() => Promise.reject(new Error("refused")));
    render(<ActionBoard items={ITEMS} onMove={onMove} />);

    await drag("a", "완료");

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("상태를 바꾸지 못했습니다"),
    );
    expect(within(column("진행 전")).getByText("항목 a")).toBeTruthy();
    expect(within(column("완료")).queryByText("항목 a")).toBeNull();
    expect(card("a").getAttribute("draggable")).toBe("true");
  });

  it("does not take a drop on the card's own column", async () => {
    const onMove = vi.fn(() => Promise.resolve());
    render(<ActionBoard items={ITEMS} onMove={onMove} />);

    const dataTransfer = transfer();
    fireEvent.dragStart(card("a"), { dataTransfer });
    await tick();
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

  it("leaves a candidate where it is: the band is a question, not a status", async () => {
    const candidate = item("d", "needs_confirmation", { is_candidate: true });
    const onMove = vi.fn(() => Promise.resolve());
    render(<ActionBoard items={[...ITEMS, candidate]} onMove={onMove} />);

    expect(card("d").getAttribute("draggable")).toBe("false");
    await drag("d", "진행 전");

    expect(onMove).not.toHaveBeenCalled();
  });

  it("changes nothing on the board inside dragstart itself, only a tick later", async () => {
    // mkkim68, review of #710: a re-render of the card while the browser is
    // still starting the drag cancels the drag in some versions of Chrome.
    render(<ActionBoard items={ITEMS} onMove={() => Promise.resolve()} />);
    const dataTransfer = transfer();

    fireEvent.dragStart(card("a"), { dataTransfer });
    // No column accepts yet: the board has not been told a drag is under way.
    expect(fireEvent.dragOver(column("완료"), { dataTransfer })).toBe(true);

    await tick();
    expect(fireEvent.dragOver(column("완료"), { dataTransfer })).toBe(false);
  });

  it("forgets a drag that ended before the board heard of it", async () => {
    const onMove = vi.fn(() => Promise.resolve());
    render(<ActionBoard items={ITEMS} onMove={onMove} />);
    const dataTransfer = transfer();

    fireEvent.dragStart(card("a"), { dataTransfer });
    fireEvent.dragEnd(card("a"), { dataTransfer });
    await tick();

    expect(fireEvent.dragOver(column("완료"), { dataTransfer })).toBe(true);
    expect(onMove).not.toHaveBeenCalled();
  });

  it("says so after a drop that confirmed an item, and offers no undo", async () => {
    render(<ActionBoard items={ITEMS} onMove={() => Promise.resolve()} />);

    await drag("b", "진행 전");

    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("확정했습니다"));
    expect(screen.queryByText(/되돌리기|취소/)).toBeNull();
  });

  it("says nothing after a move between the other columns", async () => {
    const onMove = vi.fn(() => Promise.resolve());
    render(<ActionBoard items={ITEMS} onMove={onMove} />);

    await drag("a", "완료");

    await waitFor(() => expect(card("a").getAttribute("aria-busy")).toBeNull());
    expect(onMove).toHaveBeenCalledOnce();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("does not say an item was confirmed when the server refused", async () => {
    render(<ActionBoard items={ITEMS} onMove={() => Promise.reject(new Error("refused"))} />);

    await drag("b", "진행 전");

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("takes the notice down when the next drag starts", async () => {
    render(<ActionBoard items={ITEMS} onMove={() => Promise.resolve()} />);
    await drag("b", "진행 전");
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());

    fireEvent.dragStart(card("a"), { dataTransfer: transfer() });
    await tick();

    expect(screen.queryByRole("status")).toBeNull();
  });

  it("does not drag the card whose detail window is open", async () => {
    const onMove = vi.fn(() => Promise.resolve());
    render(<ActionBoard items={ITEMS} selectedId="a" onMove={onMove} />);

    expect(card("a").getAttribute("draggable")).toBe("false");
    expect(card("c").getAttribute("draggable")).toBe("true");
    await drag("a", "진행 중");
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

  it("shows a grip on a card that can be dragged, and none where nothing moves (#1183)", () => {
    const candidate = item("d", "needs_confirmation", { is_candidate: true });
    render(<ActionBoard items={[...ITEMS, candidate]} onMove={() => Promise.resolve()} />);

    expect(card("a").querySelector("[data-drag-handle]")).not.toBeNull();
    expect(card("a").className).toContain("cursor-grab");
    expect(card("d").querySelector("[data-drag-handle]")).toBeNull();
    cleanup();

    render(<ActionBoard items={ITEMS} />);
    expect(card("a").querySelector("[data-drag-handle]")).toBeNull();
  });
});
