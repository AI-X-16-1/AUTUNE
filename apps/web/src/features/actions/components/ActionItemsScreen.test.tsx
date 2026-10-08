import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActionItemsScreen } from "./ActionItemsScreen";
import { POLL_MS } from "./ReExtract";
import type { ExtractionState } from "../api";

// An empty board is three different things: the first run is not in yet, it
// never came, or the meeting produced nothing. Only the last used to be said,
// of all three (the user, dev, 2026-10-08). The control that reads the state
// and the two lists that say it are real here; everything else is a stub.

const get = vi.fn<(meetingId: string) => Promise<ExtractionState>>();
const reload = vi.fn(async () => undefined);
const reads = { decisions: 0 };
vi.mock("../api", () => ({
  getExtractionState: (meetingId: string) => get(meetingId),
  requestExtraction: vi.fn(),
  listProjects: async () => [],
  bulkActionItems: vi.fn(),
}));
vi.mock("../hooks/useActionItems", () => ({
  useActionItems: () => ({
    items: [],
    settled: true,
    error: null,
    loading: false,
    reload,
    add: vi.fn(),
    edit: vi.fn(),
    remove: vi.fn(),
  }),
}));
vi.mock("../hooks/useDecisionReview", async () => {
  const { useEffect } = await import("react");
  return {
    useDecisionReview: () => {
      useEffect(() => {
        reads.decisions += 1; // once per mount: the list reads itself
      }, []);
      return {
        review: { decisions: [], pending_decisions: 0, ambiguous_agreements: [] },
        loading: false,
        error: null,
        setStatus: vi.fn(),
        reword: vi.fn(),
        add: vi.fn(),
        remove: vi.fn(),
      };
    },
  };
});
vi.mock("./ActionBoard", () => ({ ActionBoard: () => null }));
vi.mock("./ActionDetailDrawer", () => ({ ActionDetailDrawer: () => null }));
vi.mock("./CalendarConnect", () => ({ CalendarConnect: () => null }));
vi.mock("./CarriedOverActions", () => ({ CarriedOverActions: () => null }));
vi.mock("./JiraConnect", () => ({ JiraConnect: () => null }));
vi.mock("./MyConfirmations", () => ({ MyConfirmations: () => null }));
vi.mock("./SlackConnect", () => ({ SlackConnect: () => null }));
vi.mock("./NotionConnect", () => ({ NotionConnect: () => null }));
vi.mock("./ProjectFilter", () => ({ ProjectFilter: () => null }));

const FINE: ExtractionState = {
  extracted_at: "2026-10-08T05:02:00Z",
  failures: 0,
  failed_at: null,
  will_retry: false,
  not_published: false,
  partly_unread: false,
  requested: false,
  requested_at: null,
  in_progress: false,
  overdue: false,
  read_nothing: false,
};
const RUNNING: ExtractionState = { ...FINE, extracted_at: null, in_progress: true };

const settle = async (ms = 0) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};
const NO_ITEMS = /이 회의에서 추출된 액션 아이템이 없습니다/;
const NO_DECISIONS = /이 회의에서 제안된 결정이 없습니다/;

beforeEach(() => {
  vi.useFakeTimers();
  reads.decisions = 0;
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  get.mockReset();
  reload.mockClear();
});

describe("ActionItemsScreen, an empty meeting", () => {
  it("says 'none' of a meeting that was extracted and has none", async () => {
    get.mockResolvedValueOnce(FINE);
    render(<ActionItemsScreen meetingId="mtg_1" />);
    await settle();

    expect(screen.getByText(NO_ITEMS)).toBeTruthy();
    expect(screen.getByText(NO_DECISIONS)).toBeTruthy();
  });

  it("says 'not yet' while the first run is going, and reads both lists when it is in", async () => {
    get.mockResolvedValueOnce(RUNNING);
    render(<ActionItemsScreen meetingId="mtg_1" />);
    await settle();

    expect(screen.queryByText(NO_ITEMS)).toBeNull();
    expect(screen.queryByText(NO_DECISIONS)).toBeNull();
    expect(screen.getByText(/액션 아이템을 추출하고 있습니다\. 끝나면/)).toBeTruthy();
    expect(screen.getByText(/결정을 추출하고 있습니다\. 끝나면/)).toBeTruthy();
    expect(reload).not.toHaveBeenCalled();
    expect(reads.decisions).toBe(1);

    get.mockResolvedValueOnce(FINE);
    await settle(POLL_MS);

    expect(reload).toHaveBeenCalledOnce();
    expect(reads.decisions).toBe(2);
    // Read again and still empty: now it is true that there are none.
    expect(screen.getByText(NO_ITEMS)).toBeTruthy();
    expect(screen.getByText(NO_DECISIONS)).toBeTruthy();
  });

  it("says 'not extracted' of a meeting whose run never came", async () => {
    get.mockResolvedValueOnce({ ...RUNNING, in_progress: false, overdue: true });
    render(<ActionItemsScreen meetingId="mtg_1" />);
    await settle();

    expect(screen.queryByText(NO_ITEMS)).toBeNull();
    expect(screen.queryByText(NO_DECISIONS)).toBeNull();
    expect(screen.getByText(/^이 회의의 액션 아이템은 아직 추출되지 않았습니다/)).toBeTruthy();
    expect(screen.getByText(/^이 회의의 결정은 아직 추출되지 않았습니다/)).toBeTruthy();
  });
});
