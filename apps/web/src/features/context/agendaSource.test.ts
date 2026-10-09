import { afterEach, describe, expect, it, vi } from "vitest";

import { DECISIONS_AGENDA } from "./agendaSource";
import type { DecisionThreadFilter } from "./api";
import type { DecisionSummaryRead } from "./types";

// Module D's source for the new-meeting form's agenda draft (#1147): one read
// of the route S22 already uses.

const list = vi.fn<(filter: DecisionThreadFilter) => Promise<DecisionSummaryRead[]>>();
vi.mock("./api", () => ({
  listDecisionThreads: (filter: DecisionThreadFilter) => list(filter),
}));

afterEach(() => list.mockReset());

const decision = (overrides: Partial<DecisionSummaryRead>): DecisionSummaryRead => ({
  thread_id: "thr_1",
  topic_label: "검색 색인은 주 1회 다시 만든다",
  meeting_id: "mtg_old",
  change_type: "new",
  confidence: 0.9,
  updated_at: "2026-10-02T09:00:00Z",
  ...overrides,
});

describe("DECISIONS_AGENDA", () => {
  it("asks for the team's decisions with no other filter", async () => {
    list.mockResolvedValue([]);

    await DECISIONS_AGENDA.lines("team_1");

    expect(list).toHaveBeenCalledWith({ team_id: "team_1" });
  });

  it("is each decision's current statement and the day it last changed, newest first", async () => {
    list.mockResolvedValue([
      decision({}),
      decision({
        thread_id: "thr_2",
        topic_label: "결제 재시도는 세 번까지",
        updated_at: "2026-10-06T02:00:00Z",
      }),
    ]);

    expect(await DECISIONS_AGENDA.lines("team_1")).toEqual([
      { title: "결제 재시도는 세 번까지", detail: "2026-10-06" },
      { title: "검색 색인은 주 1회 다시 만든다", detail: "2026-10-02" },
    ]);
  });

  it("does not reorder the list it was handed", async () => {
    const handed = [decision({}), decision({ thread_id: "thr_2", updated_at: "2026-10-06T02:00:00Z" })];
    list.mockResolvedValue(handed);

    await DECISIONS_AGENDA.lines("team_1");

    expect(handed.map((row) => row.thread_id)).toEqual(["thr_1", "thr_2"]);
  });
});
