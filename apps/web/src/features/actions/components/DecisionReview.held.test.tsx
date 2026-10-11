import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DecisionReview, HELD_BACK } from "./DecisionReview";
import type { MeetingReview, ReviewDecision } from "../types";

// A confirmed decision whose copy the outbound check refuses was told to
// nobody: a warning in the log, by id (found 2026-10-09). An item in the same
// state says so on its card; the decision's row now does too.

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

const decision = (id: string, over: Partial<ReviewDecision> = {}): ReviewDecision => ({
  id,
  statement: `${id} 결정`,
  model_statement: `${id} 결정`,
  confidence: 0.8,
  origin: "model",
  needs_recheck: false,
  status: "confirmed",
  suggested: null,
  source_utterance_ids: [],
  summary: null,
  ...over,
});

const shown = (decisions: ReviewDecision[]) => {
  review.mockReturnValue({
    meeting_id: "mtg_1",
    decisions,
    ambiguous_agreements: [],
    action_items: [],
    pending_decisions: 0,
  });
  render(<DecisionReview meetingId="mtg_1" />);
  return within(screen.getByRole("region", { name: "결정" })).getAllByRole("listitem");
};

afterEach(() => {
  cleanup();
  review.mockReset();
});

describe("a decision whose copy was held back", () => {
  it("says so on its own row, with the way out, and on no other row", () => {
    const [held, sent, old] = shown([
      decision("dec_1", { held_back: true }),
      decision("dec_2", { held_back: false }),
      // A server from before the mark sends no key at all.
      decision("dec_3"),
    ]);

    const notice = within(held as HTMLElement).getByRole("status");
    expect(notice.textContent).toBe(HELD_BACK);
    expect(HELD_BACK).toContain("개인정보로 보이는 값이 있어 보내지 않았습니다");
    expect(HELD_BACK).toContain("문장을 고치면");
    expect(within(held as HTMLElement).getByRole("button", { name: "문장 고치기" })).toBeTruthy();
    expect(sent?.textContent).not.toContain("개인정보");
    expect(old?.textContent).not.toContain("개인정보");
  });

  it("names no kind of value", () => {
    const [held] = shown([decision("dec_1", { held_back: true })]);

    expect(held?.textContent).not.toMatch(/전화|이메일|주민|계좌|카드|phone|email/);
  });
});
