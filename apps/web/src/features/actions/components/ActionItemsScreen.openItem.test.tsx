import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionItemsScreen } from "./ActionItemsScreen";
import type { ActionItemRead } from "../types";

// An item pressed on the 요약 tab arrives here as `?item=<id>` and opens in
// its window (#1183). Only which item the window holds matters here.

vi.mock("../api", () => ({ listProjects: async () => [], bulkActionItems: vi.fn() }));
vi.mock("../hooks/useActionItems", () => ({
  useActionItems: () => ({
    items: [
      { id: "act_1", meeting_id: "mtg_1", description: "설문 문항 다시 쓰기" },
      { id: "act_2", meeting_id: "mtg_1", description: "QA 일정 확인" },
    ] as ActionItemRead[],
    settled: true,
    error: null,
    reload: vi.fn(),
    readTitles: vi.fn(),
    add: vi.fn(),
    edit: vi.fn(),
    close: vi.fn(),
    remove: vi.fn(),
  }),
}));
vi.mock("../hooks/useTitleReads", () => ({ useTitleReads: () => undefined }));
vi.mock("./ActionDetailDrawer", () => ({
  ActionDetailDrawer: ({ item }: { item: ActionItemRead }) => <aside aria-label="상세">{item.id}</aside>,
}));
vi.mock("./ActionBoard", () => ({ ActionBoard: () => null }));
vi.mock("./ReExtract", () => ({ ReExtract: () => null, notExtracted: () => null }));
vi.mock("./DecisionReview", () => ({ DecisionReview: () => null }));
vi.mock("./CalendarConnect", () => ({ CalendarConnect: () => null }));
vi.mock("./CarriedOverActions", () => ({ CarriedOverActions: () => null }));
vi.mock("./JiraConnect", () => ({ JiraConnect: () => null }));
vi.mock("./MyConfirmations", () => ({ MyConfirmations: () => null }));
vi.mock("./SlackConnect", () => ({ SlackConnect: () => null }));
vi.mock("./NotionConnect", () => ({ NotionConnect: () => null }));
vi.mock("./ProjectFilter", () => ({ ProjectFilter: () => null }));

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("ActionItemsScreen, an item asked for in the address", () => {
  it("opens the item the 요약 tab pointed at", () => {
    window.history.replaceState(null, "", "/meetings/mtg_1/actions?item=act_2");

    render(<ActionItemsScreen meetingId="mtg_1" />);

    expect(screen.getByRole("complementary", { name: "상세" }).textContent).toBe("act_2");
  });

  it("opens nothing for an id the meeting does not have, or with none asked for", () => {
    window.history.replaceState(null, "", "/meetings/mtg_1/actions?item=act_9");
    render(<ActionItemsScreen meetingId="mtg_1" />);
    expect(screen.queryByRole("complementary", { name: "상세" })).toBeNull();
    cleanup();

    window.history.replaceState(null, "", "/meetings/mtg_1/actions");
    render(<ActionItemsScreen meetingId="mtg_1" />);
    expect(screen.queryByRole("complementary", { name: "상세" })).toBeNull();
  });
});
