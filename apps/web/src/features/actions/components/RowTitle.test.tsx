import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionBoard } from "./ActionBoard";
import { DecisionReview } from "./DecisionReview";
import { rowTitle, shortTitle } from "../title";
import type { ActionItemRead, MeetingReview, ReviewDecision } from "../types";

// A card's and a decision row's top line is the row's own short title when
// the server wrote one -- a summary of twenty characters or fewer, ended by a
// noun (module B's owner, 2026-10-09) -- and the sentence cut when it did not. The
// sentence itself stays one step away either way.

const review = vi.fn<() => MeetingReview>();
vi.mock("../hooks/useDecisionReview", () => ({
  useDecisionReview: () => ({
    review: review(),
    loading: false,
    error: null,
    setStatus: vi.fn(),
    reword: vi.fn(),
    add: vi.fn(),
    remove: vi.fn(),
  }),
}));

afterEach(() => {
  cleanup();
  review.mockReset();
});

const LONG = "다음 주 화요일까지 결제 화면 오류 로그를 모아서 정리 예정";
const TITLE = "결제 화면 오류 로그 정리";
const CUT = shortTitle(LONG).shown;

function item(id: string, description: string, title?: string | null): ActionItemRead {
  return {
    id,
    meeting_id: "mtg_1",
    description,
    title,
    status: "todo",
    is_candidate: false,
  } as ActionItemRead;
}

function decision(id: string, statement: string, title?: string | null): ReviewDecision {
  return {
    id,
    statement,
    title,
    model_statement: statement,
    confidence: 0.8,
    origin: "model",
    needs_recheck: false,
    status: "pending",
    suggested: null,
    source_utterance_ids: [],
    summary: null,
  };
}

function decisions(...rows: ReviewDecision[]): HTMLElement {
  review.mockReturnValue({
    meeting_id: "mtg_1",
    decisions: rows,
    ambiguous_agreements: [],
    action_items: [],
    pending_decisions: rows.length,
  });
  render(<DecisionReview meetingId="mtg_1" />);
  return screen.getByRole("region", { name: "결정" });
}

describe("rowTitle", () => {
  it("is the row's own title when it has one, and says the sentence is longer", () => {
    expect(rowTitle(TITLE, LONG)).toEqual({ shown: TITLE, cut: true });
  });

  it("is the sentence cut when there is no title", () => {
    expect(rowTitle(null, LONG)).toEqual({ shown: CUT, cut: true });
    expect(rowTitle(undefined, LONG)).toEqual({ shown: CUT, cut: true });
    expect(rowTitle("  ", LONG)).toEqual({ shown: CUT, cut: true });
  });

  it("does not show a title longer than the limit", () => {
    expect(rowTitle("결제 화면에서 나는 오류 로그를 전부 모아 정리", LONG).shown).toBe(CUT);
  });

  it("has nothing more to open when the title is the sentence", () => {
    expect(rowTitle("보고서 정리", "보고서 정리")).toEqual({ shown: "보고서 정리", cut: false });
  });
});

describe("an action card's top line", () => {
  it("shows the item's title, and the whole sentence on hover", () => {
    render(<ActionBoard items={[item("a", LONG, TITLE)]} onMove={() => Promise.resolve()} />);

    const line = screen.getByText(TITLE);
    expect(line.getAttribute("title")).toBe(LONG);
    expect(screen.queryByText(CUT)).toBeNull();
  });

  it("shows the sentence cut for an item with no title", () => {
    render(
      <ActionBoard
        items={[item("a", LONG, null), item("b", LONG)]}
        onMove={() => Promise.resolve()}
      />,
    );

    expect(screen.getAllByText(CUT)).toHaveLength(2);
  });
});

describe("a decision row's top line", () => {
  const STATEMENT = "배포는 다음 주 금요일로 미루기로 함 (담당 민경, 기한 2026-10-16)";
  const SHORT = "배포 다음 주 금요일로 연기";

  it("shows the decision's title and opens to the whole statement", () => {
    const list = decisions(decision("dec_1", STATEMENT, SHORT));

    const line = within(list).getByRole("button", { name: SHORT });
    expect(line.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(line);

    // The whole statement, its owner and deadline with it, as the row writes a date.
    const whole = within(list).getByRole("button", { expanded: true });
    expect(whole.textContent).toContain("배포는 다음 주 금요일로 미루기로 함 (담당 민경, 기한 ");
    expect(within(list).queryByText(SHORT)).toBeNull();
  });

  it("shows the statement cut for a decision with no title", () => {
    const list = decisions(decision("dec_1", STATEMENT, null));

    const cut = shortTitle(STATEMENT).shown;
    expect(cut.endsWith("…")).toBe(true);
    expect(within(list).getByRole("button", { name: cut })).toBeTruthy();
  });
});
