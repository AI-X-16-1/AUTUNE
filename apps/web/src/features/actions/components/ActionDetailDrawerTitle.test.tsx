import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionDetailDrawer } from "./ActionDetailDrawer";
import type { ActionItemRead } from "../types";

// The window opens under the line the card was pressed by (the user,
// 2026-10-09): the short title as its heading, the whole sentence below it.

// The quotation is fetched when the window opens; no network here.
vi.mock("../hooks/useSourceUtterances", () => ({
  useSourceUtterances: () => ({ status: "loading" }),
}));

afterEach(cleanup);

const SENTENCE = "결제 화면 시안을 금요일까지 정리해서 디자인 팀에 공유할 예정";

function open(fields: Partial<ActionItemRead>) {
  const item = {
    id: "a",
    meeting_id: "mtg_1",
    description: SENTENCE,
    status: "todo",
    confidence: 0.92,
    is_candidate: false,
    ...fields,
  } as ActionItemRead;
  render(<ActionDetailDrawer item={item} onClose={vi.fn()} />);
  return screen.getByRole("banner");
}

describe("ActionDetailDrawer, the heading", () => {
  it("is the item's short title, with the whole sentence under it", () => {
    const header = open({ title: "결제 화면 시안 공유" });

    expect(within(header).getByRole("heading", { level: 2 }).textContent).toBe(
      "결제 화면 시안 공유",
    );
    expect(within(header).getByText(SENTENCE).tagName).toBe("P");
  });

  it("is the sentence cut as the card cuts it when the item has no title", () => {
    const header = open({ title: null });

    const heading = within(header).getByRole("heading", { level: 2 }).textContent ?? "";
    expect(heading.endsWith("…")).toBe(true);
    expect([...heading].length).toBeLessThanOrEqual(20);
    expect(SENTENCE.startsWith(heading.slice(0, -1).trimEnd())).toBe(true);
    expect(within(header).getByText(SENTENCE).tagName).toBe("P");
  });

  it("shows a sentence short enough to be the top line once", () => {
    const header = open({ description: "배포 일정 공유", title: null });

    expect(within(header).getByRole("heading", { level: 2 }).textContent).toBe("배포 일정 공유");
    expect(within(header).getAllByText("배포 일정 공유")).toHaveLength(1);
  });

  it("shows a title that says the whole sentence once", () => {
    const header = open({ description: "배포 일정 공유", title: "배포 일정 공유" });

    expect(within(header).getAllByText("배포 일정 공유")).toHaveLength(1);
  });

  it("does not take a title longer than a title can be", () => {
    const header = open({ title: SENTENCE + " 그리고 더" });

    const heading = within(header).getByRole("heading", { level: 2 }).textContent ?? "";
    expect([...heading].length).toBeLessThanOrEqual(20);
    expect(within(header).getByText(SENTENCE).tagName).toBe("P");
  });
});
