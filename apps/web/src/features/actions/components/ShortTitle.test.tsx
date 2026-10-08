import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionBoard } from "./ActionBoard";
import { DecisionReview } from "./DecisionReview";
import type { ActionItemRead, MeetingReview, ReviewDecision } from "../types";

// The top line of a card and of a decision row is shown at twenty characters
// (the user, 2026-10-08). What is stored is not what is cut: the whole
// sentence has to stay one step away, and a short one has to stay as it is.

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

const LONG = "마케팅 메일은 출시 다음 날인 수요일 오전에 보내기로 했습니다";
const SHOWN = "마케팅 메일은 출시 다음 날인…";
const SHORT = "시안은 제가 챙겨 볼게요.";

function item(id: string, description: string): ActionItemRead {
  return {
    id,
    meeting_id: "mtg_1",
    description,
    status: "todo",
    is_candidate: false,
  } as ActionItemRead;
}

function decision(id: string, statement: string): ReviewDecision {
  return {
    id,
    statement,
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

describe("an action card's top line", () => {
  it("shows a long sentence cut at twenty characters, the whole of it on hover", () => {
    render(<ActionBoard items={[item("a", LONG)]} onMove={() => Promise.resolve()} />);

    const line = screen.getByText(SHOWN);
    expect(line.getAttribute("title")).toBe(LONG);
    expect(screen.queryByText(LONG)).toBeNull();
  });

  it("shows a short sentence as it is", () => {
    render(<ActionBoard items={[item("a", SHORT)]} onMove={() => Promise.resolve()} />);

    expect(screen.getByText(SHORT)).toBeTruthy();
  });
});

describe("a decision row's top line", () => {
  it("shows a long statement cut at twenty characters", () => {
    const list = decisions(decision("dec_1", LONG));

    const line = within(list).getByRole("button", { name: SHOWN });
    expect(line.getAttribute("aria-expanded")).toBe("false");
    expect(line.getAttribute("title")).toBe(LONG);
    expect(within(list).queryByText(LONG)).toBeNull();
  });

  it("opens to the whole statement when pressed, and closes again", () => {
    const list = decisions(decision("dec_1", LONG));

    fireEvent.click(within(list).getByRole("button", { name: SHOWN }));

    const whole = within(list).getByRole("button", { name: LONG });
    expect(whole.getAttribute("aria-expanded")).toBe("true");
    expect(within(list).queryByText(SHOWN)).toBeNull();

    fireEvent.click(whole);

    expect(within(list).getByRole("button", { name: SHOWN })).toBeTruthy();
  });

  it("leaves a short statement plain text, with nothing to press", () => {
    const list = decisions(decision("dec_1", SHORT));

    expect(within(list).getByText(SHORT).tagName).toBe("P");
    expect(within(list).queryByRole("button", { name: SHORT })).toBeNull();
  });

  it("opens one row without opening the next", () => {
    const other = "환불 정책 안내 문구 변경은 법무 검토가 끝난 뒤에 하기로 했습니다";
    const list = decisions(decision("dec_1", LONG), decision("dec_2", other));

    fireEvent.click(within(list).getByRole("button", { name: SHOWN }));

    expect(within(list).getByRole("button", { name: LONG })).toBeTruthy();
    expect(within(list).queryByText(other)).toBeNull();
  });
});
