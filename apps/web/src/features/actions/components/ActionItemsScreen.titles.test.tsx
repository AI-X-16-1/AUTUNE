import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActionItemsScreen } from "./ActionItemsScreen";
import { POLL_MS } from "./ReExtract";
import type { ExtractionState } from "../api";
import type { ActionItemRead, MeetingReview, ReviewDecision } from "../types";

// A run's rows are read the moment it ends, and their titles are written by a
// task that runs after that. The tab drew the rows without titles until the
// page was loaded again (the user, dev, 2026-10-09). The control that sees the
// run end, the two hooks that read and the decision rows are real here; the
// server and the board's own drawing are stubs.

const state = vi.fn<() => Promise<ExtractionState>>();
const items = vi.fn<() => Promise<ActionItemRead[]>>();
const review = vi.fn<() => Promise<MeetingReview>>();
const patch = vi.fn<(id: string, body: unknown) => Promise<ReviewDecision>>();
vi.mock("../api", () => ({
  getExtractionState: () => state(),
  requestExtraction: vi.fn(),
  listProjects: async () => [],
  bulkActionItems: vi.fn(),
  listActionItems: () => items(),
  createActionItem: vi.fn(),
  updateActionItem: vi.fn(),
  closeActionItem: vi.fn(),
  deleteActionItem: vi.fn(),
  getReview: () => review(),
  reviewDecision: (id: string, body: unknown) => patch(id, body),
  createDecision: vi.fn(),
  deleteDecision: vi.fn(),
}));
vi.mock("./ActionBoard", () => ({
  ActionBoard: ({ items: shown }: { items: ActionItemRead[] }) => (
    <ul aria-label="board">
      {shown.map((item) => (
        <li key={item.id}>{item.title ?? `제목 없음 ${item.id}`}</li>
      ))}
    </ul>
  ),
}));
vi.mock("./ActionDetailDrawer", () => ({ ActionDetailDrawer: () => null }));
vi.mock("./CalendarConnect", () => ({ CalendarConnect: () => null }));
vi.mock("./CarriedOverActions", () => ({ CarriedOverActions: () => null }));
vi.mock("./JiraConnect", () => ({ JiraConnect: () => null }));
vi.mock("./MyConfirmations", () => ({ MyConfirmations: () => null }));
vi.mock("./SlackConnect", () => ({ SlackConnect: () => null }));
vi.mock("./NotionConnect", () => ({ NotionConnect: () => null }));
vi.mock("./ProjectFilter", () => ({ ProjectFilter: () => null }));

const FINE: ExtractionState = {
  extracted_at: "2026-10-09T07:02:00Z",
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

const SENTENCE = "결제 화면 오류 로그를 모아서 다음 회의 전까지 정리";
const SETTLED = "배포는 다음 주 금요일로 미루고 그 전에 점검을 한 번 더 하기로 함";

const item = (over: Partial<ActionItemRead> = {}) =>
  ({
    id: "act_1",
    meeting_id: "mtg_1",
    description: SENTENCE,
    status: "needs_confirmation",
    origin: "model",
    title: null,
    ...over,
  }) as ActionItemRead;

const decision = (over: Partial<ReviewDecision> = {}): ReviewDecision => ({
  id: "dec_1",
  statement: SETTLED,
  model_statement: SETTLED,
  title: null,
  confidence: 0.8,
  origin: "model",
  needs_recheck: false,
  status: "pending",
  suggested: null,
  source_utterance_ids: [],
  summary: null,
  ...over,
});

const reviewOf = (...decisions: ReviewDecision[]): MeetingReview => ({
  meeting_id: "mtg_1",
  decisions,
  ambiguous_agreements: [],
  action_items: [],
  pending_decisions: decisions.filter((d) => d.status === "pending").length,
});

const settle = async (ms = 0) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};

const board = () => within(screen.getByRole("list", { name: "board" }));
const decisions = () => within(screen.getByRole("region", { name: "결정" }));

/** The screen at the moment a first run has ended and its rows were read. */
const afterARun = async (rows: { item: ActionItemRead; decision: ReviewDecision }) => {
  state.mockResolvedValueOnce(RUNNING);
  items.mockResolvedValue([]);
  review.mockResolvedValue(reviewOf());
  render(<ActionItemsScreen meetingId="mtg_1" />);
  await settle();

  state.mockResolvedValueOnce(FINE);
  items.mockResolvedValue([rows.item]);
  review.mockResolvedValue(reviewOf(rows.decision));
  await settle(POLL_MS);
};

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  state.mockReset();
  items.mockReset();
  review.mockReset();
  patch.mockReset();
});

describe("ActionItemsScreen, the titles of a run's rows", () => {
  it("shows the titles written after the run, with no reload of the page", async () => {
    await afterARun({ item: item(), decision: decision() });
    expect(board().getByText("제목 없음 act_1")).toBeTruthy();
    expect(decisions().queryByText("배포 금요일로 연기")).toBeNull();

    items.mockResolvedValue([item({ title: "결제 화면 오류 로그 정리" })]);
    review.mockResolvedValue(reviewOf(decision({ title: "배포 금요일로 연기" })));
    await settle(10_000);

    expect(board().getByText("결제 화면 오류 로그 정리")).toBeTruthy();
    expect(decisions().getByText("배포 금요일로 연기")).toBeTruthy();
  });

  it("asks no more once every row has its title", async () => {
    await afterARun({ item: item(), decision: decision() });
    items.mockResolvedValue([item({ title: "결제 화면 오류 로그 정리" })]);
    review.mockResolvedValue(reviewOf(decision({ title: "배포 금요일로 연기" })));
    await settle(10_000);
    const asked = { items: items.mock.calls.length, review: review.mock.calls.length };

    await settle(120_000);

    expect(items.mock.calls.length).toBe(asked.items);
    expect(review.mock.calls.length).toBe(asked.review);
  });

  it("asks three times and no more for a title that never comes", async () => {
    await afterARun({ item: item(), decision: decision() });
    const asked = { items: items.mock.calls.length, review: review.mock.calls.length };

    await settle(60_000);
    expect(items.mock.calls.length).toBe(asked.items + 3);
    expect(review.mock.calls.length).toBe(asked.review + 3);

    await settle(600_000);
    expect(items.mock.calls.length).toBe(asked.items + 3);
    expect(review.mock.calls.length).toBe(asked.review + 3);
    expect(board().getByText("제목 없음 act_1")).toBeTruthy();
  });

  it("asks nothing for rows that are given no title: a person's own", async () => {
    await afterARun({
      item: item({ origin: "user" }),
      decision: decision({ statement: "배포는 금요일로 미룸" }),
    });
    const asked = { items: items.mock.calls.length, review: review.mock.calls.length };

    await settle(120_000);

    expect(items.mock.calls.length).toBe(asked.items);
    expect(review.mock.calls.length).toBe(asked.review);
  });

  it("asks nothing on a page that was only opened", async () => {
    state.mockResolvedValueOnce(FINE);
    items.mockResolvedValue([item()]);
    review.mockResolvedValue(reviewOf(decision()));
    render(<ActionItemsScreen meetingId="mtg_1" />);
    await settle();
    expect(board().getByText("제목 없음 act_1")).toBeTruthy();

    await settle(120_000);

    expect(items).toHaveBeenCalledOnce();
    expect(review).toHaveBeenCalledOnce();
  });

  it("does not undo what a person did while the titles were being read", async () => {
    await afterARun({ item: item(), decision: decision() });
    // The read for the titles is asked at ten seconds and answers later, with
    // the decision as it was when the server read it: still pending.
    let answer: (value: MeetingReview) => void = () => undefined;
    review.mockReturnValue(new Promise<MeetingReview>((resolve) => (answer = resolve)));
    await settle(10_000);

    patch.mockResolvedValue(decision({ status: "confirmed" }));
    fireEvent.click(decisions().getByRole("button", { name: "확정" }));
    await settle();
    expect(decisions().getByText("확인 필요 0")).toBeTruthy();

    answer(reviewOf(decision({ title: "배포 금요일로 연기", status: "pending" })));
    await settle();

    expect(decisions().getByText("배포 금요일로 연기")).toBeTruthy();
    expect(decisions().getByText("확인 필요 0")).toBeTruthy();
    expect(decisions().queryByRole("button", { name: "확정" })).toBeNull();
  });

  it("says nothing and keeps the rows when a read for the titles fails", async () => {
    await afterARun({ item: item(), decision: decision() });
    items.mockRejectedValue(new Error("offline"));
    review.mockRejectedValue(new Error("offline"));

    await settle(10_000);

    expect(board().getByText("제목 없음 act_1")).toBeTruthy();
    expect(screen.queryByText(/불러오지 못/)).toBeNull();
  });
});
