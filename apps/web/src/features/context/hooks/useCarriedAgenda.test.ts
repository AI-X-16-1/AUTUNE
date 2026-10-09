import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useCarriedAgenda } from "./useCarriedAgenda";
import type { CarriedGroup, CarriedSource } from "./useCarriedAgenda";
import type { BriefRead } from "../types";

// What the hook hands back on every render, not only on the one the screen
// settles on: the render in which the brief changes comes before the effect
// that empties what was held for the brief before it.

afterEach(cleanup);

const brief = (meetingId: string, earlier: string): BriefRead => ({
  meeting_id: meetingId,
  title: "주간 회의",
  starts_at: null,
  recap: { meeting_id: earlier, title: "지난 주간 회의", day: "2026-10-05", topics: [], decisions: [] },
  recap_gone: false,
  match_reason: "series",
  agenda: [],
  sent_at: null,
});

describe("useCarriedAgenda", () => {
  it("hands back nothing of the earlier brief in the very render the brief changes", async () => {
    // Answers at once for the first brief, and never for the second.
    const lines = vi.fn<CarriedSource["lines"]>((earlier) =>
      earlier === "old_a" ? Promise.resolve([{ title: "A 회의의 갭" }]) : new Promise(() => undefined),
    );
    const sources = [{ label: "지난 회의의 미해결 갭", lines }];
    const renders: { earlier: string; groups: readonly CarriedGroup[] }[] = [];
    const view = renderHook(
      ({ current }) => {
        const groups = useCarriedAgenda(current, sources);
        renders.push({ earlier: current.recap?.meeting_id ?? "", groups });
        return groups;
      },
      { initialProps: { current: brief("mtg_a", "old_a") } },
    );
    await waitFor(() => expect(view.result.current).toHaveLength(1));

    view.rerender({ current: brief("mtg_b", "old_b") });
    await waitFor(() => expect(lines).toHaveBeenCalledWith("old_b"));

    const underTheNewBrief = renders.filter((render) => render.earlier === "old_b");
    expect(underTheNewBrief.length).toBeGreaterThan(0);
    expect(underTheNewBrief.map((render) => render.groups)).toEqual(underTheNewBrief.map(() => []));
  });
});
