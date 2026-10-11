import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActionCard } from "./ActionCard";
import { ActionDetailDrawer } from "./ActionDetailDrawer";
import { CarriedOverActions } from "./CarriedOverActions";
import type { ActionItemRead, CarriedOver } from "../types";

// An item's due date reads the same on every screen of this feature (the user,
// 2026-10-09): "10월 13일 화", the year only when it is not this one. What is
// stored and what an input holds stay as the server writes a day.

vi.mock("../hooks/useSourceUtterances", () => ({
  useSourceUtterances: () => ({ status: "loading" }),
}));
const carried = vi.fn<() => Promise<CarriedOver>>();
vi.mock("../api", async (original) => ({
  ...(await original<typeof import("../api")>()),
  getCarriedOver: () => carried(),
}));

const item = (over: Partial<ActionItemRead>): ActionItemRead =>
  ({
    id: "a",
    meeting_id: "mtg_1",
    description: "배포 일정 공유",
    status: "todo",
    confidence: 0.92,
    is_candidate: false,
    due_date: "2026-10-13",
    ...over,
  }) as ActionItemRead;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 9, 12));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  carried.mockReset();
});

describe("an item's due date", () => {
  it("is written on a board card as a day, the year only when it is another one", () => {
    render(
      <>
        <ActionCard item={item({})} />
        <ActionCard item={item({ id: "b", due_date: "2027-01-04" })} />
      </>,
    );

    expect(screen.getByText("10월 13일 화")).toBeTruthy();
    expect(screen.getByText("2027년 1월 4일 월")).toBeTruthy();
    expect(document.body.textContent).not.toContain("2026-10-13");
  });

  it("is written the same way in the item's window, and 없음 when there is none", () => {
    const { unmount } = render(
      <ActionDetailDrawer item={item({})} onClose={() => {}} onDelete={() => Promise.resolve()} />,
    );
    expect(screen.getByText("10월 13일 화")).toBeTruthy();
    unmount();

    render(
      <ActionDetailDrawer
        item={item({ due_date: null })}
        onClose={() => {}}
        onDelete={() => Promise.resolve()}
      />,
    );
    expect(screen.getByText("없음")).toBeTruthy();
  });

  it("is written the same way on an item carried over from an earlier meeting", async () => {
    carried.mockResolvedValue({
      open: 1,
      overdue: 0,
      items: [{ ...item({}), meeting_title: "지난 회의", meeting_started_at: null }],
    });

    render(<CarriedOverActions meetingId="mtg_2" />);

    expect(await screen.findByText("10월 13일 화")).toBeTruthy();
  });

  it("is shown as it came when it is not a day as the server writes one", () => {
    render(<ActionCard item={item({ due_date: "2026-10" })} />);

    expect(screen.getByText("2026-10")).toBeTruthy();
  });
});
