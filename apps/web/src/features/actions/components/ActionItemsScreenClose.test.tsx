import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionItemsScreen } from "./ActionItemsScreen";
import type { ActionItemRead } from "../types";

// The meeting's board hands the detail window a way to close the open item
// without finishing it (#856), and that way is the list's own `close` -- not
// an edit of the status, which would leave the item looking finished.

const close = vi.fn<(id: string) => Promise<void>>(async () => undefined);
const edit = vi.fn(async () => undefined);
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
      { id: "act_1", meeting_id: "mtg_1", description: "접을 일", status: "todo" },
      { id: "act_2", meeting_id: "mtg_1", description: "남길 일", status: "todo" },
    ] as ActionItemRead[],
    settled: true,
    error: null,
    loading: false,
    reload: vi.fn(),
    add: vi.fn(),
    edit: (...args: unknown[]) => edit(...(args as [])),
    close: (id: string) => close(id),
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
vi.mock("./ActionBoard", () => ({
  ActionBoard: ({
    items,
    onSelect,
  }: {
    items: ActionItemRead[];
    onSelect: (id: string) => void;
  }) => (
    <ul>
      {items.map((item) => (
        <li key={item.id}>
          <button type="button" onClick={() => onSelect(item.id)}>
            {item.description}
          </button>
        </li>
      ))}
    </ul>
  ),
}));
vi.mock("./ActionDetailDrawer", () => ({
  ActionDetailDrawer: ({
    item,
    onCloseUnfinished,
  }: {
    item: ActionItemRead;
    onCloseUnfinished?: () => Promise<void>;
  }) => (
    <aside aria-label="상세">
      {item.id}
      {onCloseUnfinished !== undefined ? (
        <button
          type="button"
          aria-label="끝내지 않고 닫기"
          onClick={() => void onCloseUnfinished()}
        />
      ) : null}
    </aside>
  ),
}));
vi.mock("./CalendarConnect", () => ({ CalendarConnect: () => null }));
vi.mock("./CarriedOverActions", () => ({ CarriedOverActions: () => null }));
vi.mock("./JiraConnect", () => ({ JiraConnect: () => null }));
vi.mock("./MyConfirmations", () => ({ MyConfirmations: () => null }));
vi.mock("./SlackConnect", () => ({ SlackConnect: () => null }));
vi.mock("./NotionConnect", () => ({ NotionConnect: () => null }));
vi.mock("./ProjectFilter", () => ({ ProjectFilter: () => null }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ActionItemsScreen, closing the open item without finishing it", () => {
  it("closes the item whose window is open, through the list's close", async () => {
    render(<ActionItemsScreen meetingId="mtg_1" />);
    fireEvent.click(await screen.findByRole("button", { name: "접을 일" }));
    expect(screen.getByLabelText("상세").textContent).toBe("act_1");

    fireEvent.click(screen.getByRole("button", { name: "끝내지 않고 닫기" }));

    await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
    expect(close).toHaveBeenCalledWith("act_1");
    expect(edit).not.toHaveBeenCalled();
  });
});
