import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionItemsScreen } from "./ActionItemsScreen";
import type { ActionItemRead } from "../types";

// The 요약 tab links an item here as `?item=<id>` (#1183), and that item's
// window is open on arrival.

vi.mock("../api", () => ({
  getExtractionState: async () => ({
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
  }),
  requestExtraction: vi.fn(),
  listProjects: async () => [],
  bulkActionItems: vi.fn(),
}));
vi.mock("../hooks/useActionItems", () => ({
  useActionItems: () => ({
    items: [
      { id: "act_1", meeting_id: "mtg_1", description: "첫 일", status: "todo" },
      { id: "act_2", meeting_id: "mtg_1", description: "둘째 일", status: "todo" },
    ] as ActionItemRead[],
    settled: true,
    error: null,
    loading: false,
    reload: vi.fn(),
    add: vi.fn(),
    edit: vi.fn(),
    close: vi.fn(),
    remove: vi.fn(),
  }),
}));
vi.mock("../hooks/useDecisionReview", () => ({
  useDecisionReview: () => ({
    review: { decisions: [], pending_decisions: 0, ambiguous_agreements: [] },
    loading: false,
    error: null,
    setStatus: vi.fn(),
    reword: vi.fn(),
    add: vi.fn(),
    remove: vi.fn(),
  }),
}));
vi.mock("./ActionBoard", () => ({ ActionBoard: () => null }));
vi.mock("./ActionDetailDrawer", () => ({
  ActionDetailDrawer: ({ item }: { item: ActionItemRead }) => (
    <aside aria-label="상세">{item.id}</aside>
  ),
}));
vi.mock("./CalendarConnect", () => ({ CalendarConnect: () => null }));
vi.mock("./CarriedOverActions", () => ({ CarriedOverActions: () => null }));
vi.mock("./JiraConnect", () => ({ JiraConnect: () => null }));
vi.mock("./MyConfirmations", () => ({ MyConfirmations: () => null }));
vi.mock("./SlackConnect", () => ({ SlackConnect: () => null }));
vi.mock("./NotionConnect", () => ({ NotionConnect: () => null }));
vi.mock("./ProjectFilter", () => ({ ProjectFilter: () => null }));

const visit = (search: string) =>
  window.history.pushState({}, "", `/meetings/mtg_1/actions${search}`);

afterEach(() => {
  cleanup();
  window.history.pushState({}, "", "/");
});

describe("ActionItemsScreen, arriving with ?item=", () => {
  it("opens the window of the item the address names", async () => {
    visit("?item=act_2");

    render(<ActionItemsScreen meetingId="mtg_1" />);

    expect((await screen.findByLabelText("상세")).textContent).toBe("act_2");
  });

  it("opens nothing when the address names none", async () => {
    visit("");

    render(<ActionItemsScreen meetingId="mtg_1" />);
    await Promise.resolve();

    expect(screen.queryByLabelText("상세")).toBeNull();
  });

  it("opens nothing for an id that is not this meeting's, and not an empty one", async () => {
    visit("?item=act_elsewhere");
    const { unmount } = render(<ActionItemsScreen meetingId="mtg_1" />);
    await Promise.resolve();
    expect(screen.queryByLabelText("상세")).toBeNull();
    unmount();

    visit("?item=");
    render(<ActionItemsScreen meetingId="mtg_1" />);
    await Promise.resolve();
    expect(screen.queryByLabelText("상세")).toBeNull();
  });
});
