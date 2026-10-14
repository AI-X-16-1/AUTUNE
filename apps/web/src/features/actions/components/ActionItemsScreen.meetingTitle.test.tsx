import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActionItemsScreen } from "./ActionItemsScreen";
import type { ExtractionState } from "../api";
import type { MeetingSummary } from "../types";

// The heading of a meeting's 할 일 tab used to carry the meeting's id in mono
// type under "회의 검토" (the full-service look, 2026-10-14). It says the
// meeting's title now, and where the title cannot be read it says nothing: an
// internal id is never what a person is shown. The summary read, the board and
// the decisions are stubs; the hook that reads the title is the real one.

const MEETING_ID = "mtg_5c1794b72ef44c338c4065e6ad70030d";

const summary = vi.fn<(meetingId: string) => Promise<MeetingSummary>>();
vi.mock("../api", () => ({
  getSummary: (meetingId: string) => summary(meetingId),
  getExtractionState: async (): Promise<ExtractionState> => ({
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
    items: [],
    settled: true,
    error: null,
    loading: false,
    reload: vi.fn(async () => undefined),
    readTitles: vi.fn(),
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
vi.mock("./ActionDetailDrawer", () => ({ ActionDetailDrawer: () => null }));
vi.mock("./CalendarConnect", () => ({ CalendarConnect: () => null }));
vi.mock("./CarriedOverActions", () => ({ CarriedOverActions: () => null }));
vi.mock("./JiraConnect", () => ({ JiraConnect: () => null }));
vi.mock("./MyConfirmations", () => ({ MyConfirmations: () => null }));
vi.mock("./SlackConnect", () => ({ SlackConnect: () => null }));
vi.mock("./NotionConnect", () => ({ NotionConnect: () => null }));
vi.mock("./ProjectFilter", () => ({ ProjectFilter: () => null }));
vi.mock("./DecisionReview", () => ({ DecisionReview: () => null }));
vi.mock("./ReExtract", async () => {
  const real = await vi.importActual<typeof import("./ReExtract")>("./ReExtract");
  return { ...real, ReExtract: () => null };
});

const read = (meetingTitle: string | null | undefined): MeetingSummary => ({
  meeting_id: MEETING_ID,
  meeting_title: meetingTitle,
  decisions: [],
  action_items: [],
  open_questions: 0,
  ambiguous_waiting: 0,
  note: null,
  note_updated_at: null,
});

const settle = () => act(async () => void (await Promise.resolve()));

beforeEach(() => {
  summary.mockReset();
});
afterEach(cleanup);

describe("ActionItemsScreen heading", () => {
  it("names the meeting by its title under 회의 검토", async () => {
    summary.mockResolvedValue(read("플랫폼팀 주간 회의"));
    render(<ActionItemsScreen meetingId={MEETING_ID} />);
    await settle();

    expect(screen.getByRole("heading", { name: "회의 검토" })).toBeTruthy();
    expect(screen.getByText("플랫폼팀 주간 회의")).toBeTruthy();
    expect(summary).toHaveBeenCalledWith(MEETING_ID);
    expect(document.body.textContent).not.toContain(MEETING_ID);
  });

  it("shows no id while the title is still being read", async () => {
    summary.mockReturnValue(new Promise(() => undefined));
    render(<ActionItemsScreen meetingId={MEETING_ID} />);
    await settle();

    expect(document.body.textContent).not.toContain(MEETING_ID);
    expect(document.body.textContent).not.toContain("mtg_");
  });

  it("shows no id when the title cannot be read", async () => {
    summary.mockRejectedValue(new Error("boom"));
    render(<ActionItemsScreen meetingId={MEETING_ID} />);
    await settle();

    expect(screen.getByRole("heading", { name: "회의 검토" })).toBeTruthy();
    expect(document.body.textContent).not.toContain(MEETING_ID);
  });

  it.each([["   "], [null], [undefined]])("shows no id for a blank title (%j)", async (blank) => {
    summary.mockResolvedValue(read(blank));
    render(<ActionItemsScreen meetingId={MEETING_ID} />);
    await settle();

    expect(document.body.textContent).not.toContain(MEETING_ID);
  });
});
