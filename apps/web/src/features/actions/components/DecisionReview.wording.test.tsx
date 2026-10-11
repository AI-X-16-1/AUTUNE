import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DecisionReview } from "./DecisionReview";
import type { MeetingReview, ReviewDecision } from "../types";

// A decision nobody confirmed is "확인 필요", the word an unconfirmed item has
// on the board -- not "확인 대기", which read as a queue (the user, 2026-10-08).

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

const decision = (id: string, status: ReviewDecision["status"]): ReviewDecision => ({
  id,
  statement: `${id} 결정`,
  model_statement: `${id} 결정`,
  confidence: 0.8,
  origin: "model",
  needs_recheck: false,
  status,
  suggested: null,
  source_utterance_ids: [],
  summary: null,
});

afterEach(() => {
  cleanup();
  review.mockReset();
});

describe("what the decision list calls a decision nobody confirmed", () => {
  it("counts and marks it as needing confirmation, and offers to put one back there", () => {
    review.mockReturnValue({
      meeting_id: "mtg_1",
      decisions: [decision("dec_1", "pending"), decision("dec_2", "confirmed")],
      ambiguous_agreements: [],
      action_items: [],
      pending_decisions: 1,
    });

    render(<DecisionReview meetingId="mtg_1" />);

    const list = screen.getByRole("region", { name: "결정" });
    expect(within(list).getByText("확인 필요 1")).toBeTruthy();
    const [pending, confirmed] = within(list).getAllByRole("listitem");
    expect(pending?.textContent).toContain("확인 필요 ·");
    expect(confirmed?.textContent).toContain("확정 ·");
    expect(within(confirmed as HTMLElement).getByRole("button", { name: "확인 필요로 되돌리기" })).toBeTruthy();
    expect(list.textContent).not.toContain("확인 대기");
  });
});
