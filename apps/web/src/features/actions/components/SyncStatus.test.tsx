import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionCard } from "./ActionCard";
import { SyncStatus, calendarLine } from "./SyncStatus";
import type { ActionItemRead, CalendarState, SyncFailure } from "../types";

// #680: a copy that failed is said in red text, with the kind that is all the
// server keeps; an item with no calendar event says why; "다시 시도" queues
// the sync again and claims no more than that.

const retry = vi.fn<(id: string) => Promise<{ queued: boolean }>>();
vi.mock("../api", () => ({ retrySync: (id: string) => retry(id) }));

const JIRA_DOWN: SyncFailure = {
  system: "jira",
  kind: "reconnect",
  failed_at: "2026-10-02T01:00:00Z",
};

function item(failures: SyncFailure[]): ActionItemRead {
  return {
    id: "act_1",
    meeting_id: "mtg_1",
    description: "스펙 초안 공유",
    status: "todo",
    confidence: 0.9,
    is_candidate: false,
    origin: "model",
    sync_refs: [],
    sync_failures: failures,
  } as unknown as ActionItemRead;
}

afterEach(() => {
  cleanup();
  retry.mockReset();
});

describe("SyncStatus", () => {
  it("draws nothing when nothing failed and nothing is known about the calendar", () => {
    const { container } = render(<SyncStatus item={item([])} calendar={null} />);

    expect(container.textContent).toBe("");
  });

  it.each([
    ["privacy", /개인정보로 보이는 값이 있어 보내지 않았습니다/],
    ["reconnect", /연결이 끊어졌습니다/],
    ["unreachable", /응답이 없었습니다/],
    ["rejected", /요청이 거절되었습니다/],
  ] as const)("says what kind of failure it was: %s", (kind, words) => {
    render(<SyncStatus item={item([{ ...JIRA_DOWN, kind }])} calendar={null} />);

    const line = screen.getByText(words);
    expect(line.textContent).toContain("Jira 연동 실패");
    expect(line.className).toContain("color-signal-critical");
  });

  it("queues the sync again and says only that it was sent", async () => {
    retry.mockResolvedValue({ queued: true });
    render(<SyncStatus item={item([JIRA_DOWN])} calendar={null} />);

    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));

    expect((await screen.findByRole("status")).textContent).toContain("다시 보냈습니다");
    expect(retry).toHaveBeenCalledExactlyOnceWith("act_1");
    // The failure line stays: nothing here knows yet whether it went through.
    expect(screen.getByText(/Jira 연동 실패/)).toBeTruthy();
  });

  it("says so when the retry itself could not be sent", async () => {
    retry.mockRejectedValue(new Error("502"));
    render(<SyncStatus item={item([JIRA_DOWN])} calendar={null} />);

    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));

    expect((await screen.findByRole("alert")).textContent).toContain("다시 보내지 못했습니다");
  });

  it("offers no retry where nothing failed", () => {
    render(<SyncStatus item={item([])} calendar={{ state: "none", reason: "no_due_date" }} />);

    expect(screen.queryByRole("button", { name: "다시 시도" })).toBeNull();
  });
});

describe("the calendar line", () => {
  it.each([
    [{ state: "sent", reason: null }, "담당자의 캘린더에 올라가 있습니다."],
    [{ state: "none", reason: "not_confirmed" }, "확정되면 담당자의 캘린더에 올라갑니다."],
    [{ state: "none", reason: "no_due_date" }, "기한이 없어 캘린더에 올리지 않았습니다."],
    [{ state: "none", reason: null }, "담당자의 캘린더에 아직 일정이 없습니다."],
  ] as [CalendarState, string][])("%j", (calendar, words) => {
    expect(calendarLine(calendar)).toBe(words);
  });

  it("tells somebody with a typed name for an assignee what to do about it", () => {
    render(<SyncStatus item={item([])} calendar={{ state: "none", reason: "no_account" }} />);

    expect(screen.getByText(/담당자를 팀 구성원으로 지정해 주세요/)).toBeTruthy();
  });

  it("speaks of my own calendar only in the reason the server sends to me alone", () => {
    expect(calendarLine({ state: "none", reason: "not_connected" })).toContain("내 Google 캘린더");
    expect(calendarLine({ state: "none", reason: null })).not.toContain("연결");
  });
});

describe("a card", () => {
  it("says in red text which copies failed", () => {
    render(
      <ActionCard
        item={item([JIRA_DOWN, { system: "calendar", kind: "unreachable", failed_at: "x" }])}
      />,
    );

    const line = screen.getByText("연동 실패 · Jira, 캘린더");
    expect(line.className).toContain("color-signal-critical");
  });

  it("says nothing of the kind when nothing failed", () => {
    render(<ActionCard item={item([])} />);

    expect(screen.queryByText(/연동 실패/)).toBeNull();
  });
});
