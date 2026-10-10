import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionBoard } from "./ActionBoard";
import { ActionDetailDrawer } from "./ActionDetailDrawer";
import type { ActionItemRead, ActionStatus } from "../types";

// An item closed without being finished (#856). It ends in 완료 like finished
// work, so the card and the window's history are what tell the two apart.

const detail = vi.fn();
vi.mock("../hooks/useSourceUtterances", () => ({
  useSourceUtterances: () => detail(),
}));

afterEach(() => {
  cleanup();
  detail.mockReset();
});

function item(id: string, status: ActionStatus, extra: Partial<ActionItemRead> = {}) {
  return {
    id,
    meeting_id: "mtg_1",
    description: `항목 ${id}`,
    status,
    confidence: 0.92,
    is_candidate: false,
    ...extra,
  } as ActionItemRead;
}

const card = (id: string) => screen.getByText(`항목 ${id}`).closest("[role='button']") as HTMLElement;

describe("an item closed without being finished", () => {
  it("is marked 닫힘 on its card in 완료, and a finished one is not", () => {
    render(
      <ActionBoard
        items={[
          item("closed", "done", { closed_unfinished: true }),
          item("finished", "done", { closed_unfinished: false }),
          item("open", "todo"),
        ]}
        onMove={vi.fn()}
      />,
    );

    const done = screen.getByRole("region", { name: "완료" });
    const mark = within(card("closed")).getByText("닫힘");

    expect(done.contains(card("closed"))).toBe(true);
    expect(mark.getAttribute("title")).toBe("끝내지 않고 닫힘");
    expect(mark.getAttribute("aria-label")).toBe("끝내지 않고 닫힘");
    expect(within(card("finished")).queryByText("닫힘")).toBeNull();
    expect(within(card("open")).queryByText("닫힘")).toBeNull();
  });

  it("says so in the window's history, beside the edits before it", () => {
    detail.mockReturnValue({
      sources: [],
      context: [],
      related: [],
      loading: false,
      error: null,
      dmUrl: null,
      history: [
        { kind: "edited", fields: ["status"], at: "2026-10-06T01:00:00Z" },
        { kind: "closed", fields: [], at: "2026-10-07T01:00:00Z" },
      ],
    });

    render(
      <ActionDetailDrawer
        item={item("closed", "done", { closed_unfinished: true })}
        onClose={vi.fn()}
      />,
    );

    const lines = screen.getAllByRole("listitem").map((line) => line.textContent ?? "");
    expect(lines.some((line) => line.endsWith("상태 수정"))).toBe(true);
    expect(lines.some((line) => line.endsWith("끝내지 않고 닫힘"))).toBe(true);
  });
});
