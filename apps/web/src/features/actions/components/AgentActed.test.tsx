import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { announceAgentActed } from "@/shared/lib/agentActed";

import { ActionItemsScreen } from "./ActionItemsScreen";
import { CarriedOverActions } from "./CarriedOverActions";
import type { ActionItemFilter } from "../api";
import { useActionItems } from "../hooks/useActionItems";
import type { ActionItemRead, CarriedOver } from "../types";

// #1055: an owner or a due date approved on a chat card is written while the
// board is on screen. The board, the team board, the item's window and the
// carried-over list showed the old one until a reload, which ends the
// conversation (seen locally on 2026-10-09, all four). They read again when
// the assistant says it acted.

const list = vi.fn<(filter: ActionItemFilter) => Promise<ActionItemRead[]>>();
const carried = vi.fn<(meetingId: string) => Promise<CarriedOver>>();
vi.mock("../api", () => ({
  listActionItems: (filter: ActionItemFilter) => list(filter),
  getCarriedOver: (meetingId: string) => carried(meetingId),
  createActionItem: vi.fn(),
  updateActionItem: vi.fn(),
  closeActionItem: vi.fn(),
  deleteActionItem: vi.fn(),
  listProjects: async () => [],
  bulkActionItems: vi.fn(),
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
// The board and the window are stood in for by what they are handed: the
// screen gives both their item from the one list.
vi.mock("./ActionBoard", () => ({
  ActionBoard: ({
    items,
    onSelect,
  }: {
    items: ActionItemRead[];
    onSelect: (id: string) => void;
  }) => (
    <ul aria-label="보드">
      {items.map((item) => (
        <li key={item.id}>
          <button type="button" onClick={() => onSelect(item.id)}>
            {item.description}
          </button>
          {` ${item.due_date}`}
        </li>
      ))}
    </ul>
  ),
}));
vi.mock("./ActionDetailDrawer", () => ({
  ActionDetailDrawer: ({ item }: { item: ActionItemRead }) => (
    <aside aria-label="상세">{`${item.due_date} ${item.assignee_label}`}</aside>
  ),
}));
vi.mock("./CalendarConnect", () => ({ CalendarConnect: () => null }));
vi.mock("./JiraConnect", () => ({ JiraConnect: () => null }));
vi.mock("./MyConfirmations", () => ({ MyConfirmations: () => null }));
vi.mock("./SlackConnect", () => ({ SlackConnect: () => null }));
vi.mock("./NotionConnect", () => ({ NotionConnect: () => null }));
vi.mock("./ProjectFilter", () => ({ ProjectFilter: () => null }));
vi.mock("./ReExtract", () => ({ ReExtract: () => null }));

const item = (over: Partial<ActionItemRead> = {}): ActionItemRead =>
  ({
    id: "act_1",
    meeting_id: "mtg_1",
    description: "배포 점검표 정리",
    status: "todo",
    is_candidate: false,
    due_date: "2026-10-06",
    assignee_label: "도윤재",
    ...over,
  }) as ActionItemRead;

const MOVED = item({ due_date: "2026-10-16", assignee_label: "임가온" });

const left = (over: Partial<ActionItemRead>, counts: Partial<CarriedOver> = {}): CarriedOver => ({
  open: 1,
  overdue: 1,
  items: [{ ...item(over), meeting_title: "지난 회의", meeting_started_at: null }],
  ...counts,
});

const NONE: CarriedOver = { open: 0, overdue: 0, items: [] };

/** A read whose answer the test gives when it chooses. */
function held<T>() {
  let give: (value: T) => void = () => undefined;
  const promise = new Promise<T>((resolve) => {
    give = resolve;
  });
  return { promise, give };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 9, 12));
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  list.mockReset();
  carried.mockReset();
});

describe("the list of items hears the assistant", () => {
  it.each([
    ["a meeting's board", { meeting_id: "mtg_1" }],
    ["the team board", {}],
  ] as [string, ActionItemFilter][])("reads %s again", async (_name, filter) => {
    list.mockResolvedValueOnce([item()]).mockResolvedValueOnce([MOVED]);
    const { result } = renderHook(() => useActionItems(filter));
    await waitFor(() => expect(result.current.settled).toBe(true));
    expect(result.current.items[0]?.due_date).toBe("2026-10-06");

    act(() => announceAgentActed());

    await waitFor(() => expect(result.current.items[0]?.due_date).toBe("2026-10-16"));
    expect(list).toHaveBeenCalledTimes(2);
    expect(list).toHaveBeenLastCalledWith(filter);
  });

  it("keeps the items on screen while it reads, and when the read fails", async () => {
    const second = held<ActionItemRead[]>();
    list.mockResolvedValueOnce([item()]).mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useActionItems({ meeting_id: "mtg_1" }));
    await waitFor(() => expect(result.current.settled).toBe(true));

    act(() => announceAgentActed());

    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(result.current.settled).toBe(true);
    expect(result.current.items).toHaveLength(1);

    list.mockRejectedValueOnce(new Error("no answer"));
    act(() => announceAgentActed());
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.items[0]?.due_date).toBe("2026-10-06");
  });

  it("reads the meeting now open, not the one it was on", async () => {
    list.mockResolvedValue([]);
    const { result, rerender } = renderHook(
      ({ meeting }: { meeting: string }) => useActionItems({ meeting_id: meeting }),
      { initialProps: { meeting: "mtg_1" } },
    );
    await waitFor(() => expect(result.current.settled).toBe(true));
    rerender({ meeting: "mtg_2" });
    await waitFor(() => expect(result.current.settled).toBe(true));
    list.mockClear();

    act(() => announceAgentActed());

    await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
    expect(list).toHaveBeenCalledWith({ meeting_id: "mtg_2" });
  });

  it("stops listening when the screen is left", async () => {
    list.mockResolvedValue([item()]);
    const { result, unmount } = renderHook(() => useActionItems({ meeting_id: "mtg_1" }));
    await waitFor(() => expect(result.current.settled).toBe(true));
    unmount();

    act(() => announceAgentActed());

    expect(list).toHaveBeenCalledTimes(1);
  });
});

describe("a meeting's screen hears the assistant", () => {
  it("shows the new due date and owner on the board and in the item's open window", async () => {
    list.mockResolvedValueOnce([item()]).mockResolvedValueOnce([MOVED]);
    carried.mockResolvedValue(NONE);
    render(<ActionItemsScreen meetingId="mtg_1" />);
    fireEvent.click(await screen.findByRole("button", { name: "배포 점검표 정리" }));
    expect(screen.getByLabelText("상세").textContent).toBe("2026-10-06 도윤재");

    act(() => announceAgentActed());

    await waitFor(() => expect(screen.getByLabelText("상세").textContent).toBe("2026-10-16 임가온"));
    expect(screen.getByLabelText("보드").textContent).toContain("2026-10-16");
    expect(screen.getByLabelText("보드").textContent).not.toContain("2026-10-06");
  });
});

describe("the carried-over list hears the assistant", () => {
  it("shows the new due date and counts in the open popup", async () => {
    carried
      .mockResolvedValueOnce(left({}))
      .mockResolvedValueOnce(left({ due_date: "2026-10-16" }, { overdue: 0 }));
    render(<CarriedOverActions meetingId="mtg_2" />);
    const popup = await screen.findByRole("dialog", { name: "지난 회의 미완료 할 일" });
    expect(popup.textContent).toContain("10월 6일 화");
    expect(popup.textContent).toContain("기한이 지난 1건부터");

    act(() => announceAgentActed());

    await waitFor(() => expect(popup.textContent).toContain("10월 16일 금"));
    expect(popup.textContent).not.toContain("10월 6일 화");
    expect(popup.textContent).not.toContain("기한이 지난");
    expect(carried).toHaveBeenLastCalledWith("mtg_2");
  });

  it("does not put a closed popup back, even in a browser that keeps nothing", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage refused");
    });
    carried
      .mockResolvedValueOnce(left({}))
      .mockResolvedValueOnce(left({ due_date: "2026-10-16" }, { overdue: 0 }));
    render(<CarriedOverActions meetingId="mtg_2" />);
    await screen.findByRole("dialog", { name: "지난 회의 미완료 할 일" });
    fireEvent.click(screen.getByRole("button", { name: "확인" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.textContent).toContain("기한 지남 1건");

    act(() => announceAgentActed());

    await waitFor(() => expect(document.body.textContent).not.toContain("기한 지남"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("goes away when nothing is left open", async () => {
    carried.mockResolvedValueOnce(left({})).mockResolvedValueOnce(NONE);
    render(<CarriedOverActions meetingId="mtg_2" />);
    await screen.findByRole("dialog", { name: "지난 회의 미완료 할 일" });

    act(() => announceAgentActed());

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.body.textContent).not.toContain("넘어온 미완료 할 일");
  });

  it("opens the popup on the first answer to land, and drops an older one landing after", async () => {
    const first = held<CarriedOver>();
    carried
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce(left({ due_date: "2026-10-16" }, { overdue: 0 }));
    render(<CarriedOverActions meetingId="mtg_2" />);

    act(() => announceAgentActed());

    const popup = await screen.findByRole("dialog", { name: "지난 회의 미완료 할 일" });
    expect(popup.textContent).toContain("10월 16일 금");
    await act(async () => {
      first.give(left({}));
      await first.promise;
    });
    expect(popup.textContent).toContain("10월 16일 금");
    expect(popup.textContent).not.toContain("10월 6일 화");
  });

  it("stops listening when the screen is left", async () => {
    carried.mockResolvedValue(left({}));
    const { unmount } = render(<CarriedOverActions meetingId="mtg_2" />);
    await screen.findByRole("dialog", { name: "지난 회의 미완료 할 일" });
    unmount();

    act(() => announceAgentActed());

    expect(carried).toHaveBeenCalledTimes(1);
  });
});
