import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DecisionReview } from "./DecisionReview";
import type { MeetingReview, ReviewDecision } from "../types";

// A decision's deadline reads on its row as the page's other dates read (the
// user, 2026-10-09) -- and only reads so: the stored sentence is what a person
// rewords, because that is what is saved.

const review = vi.fn<() => MeetingReview>();
const reword = vi.fn();
vi.mock("../hooks/useDecisionReview", () => ({
  useDecisionReview: () => ({
    review: review(),
    loading: false,
    error: null,
    setStatus: vi.fn(),
    reword,
    add: vi.fn(),
    remove: vi.fn(),
  }),
}));

const STORED = "배포는 다음 주로 미룹니다 (담당 박지영, 기한 2026-10-13)";
const SHOWN = "배포는 다음 주로 미룹니다 (담당 박지영, 기한 10월 13일 화)";

const decision = (over: Partial<ReviewDecision>): ReviewDecision => ({
  id: "dec_1",
  statement: STORED,
  model_statement: STORED,
  confidence: 0.8,
  origin: "model",
  needs_recheck: false,
  status: "pending",
  suggested: null,
  source_utterance_ids: ["utt_1"],
  summary: null,
  ...over,
});

function show(...decisions: ReviewDecision[]): HTMLElement {
  review.mockReturnValue({
    meeting_id: "mtg_1",
    decisions,
    ambiguous_agreements: [],
    action_items: [],
    pending_decisions: decisions.length,
  });
  render(<DecisionReview meetingId="mtg_1" />);
  return screen.getByRole("region", { name: "결정" });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 9, 12));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  review.mockReset();
  reword.mockReset();
});

describe("a decision's deadline on its row", () => {
  it("reads as the page writes a day, short and whole", () => {
    const list = show(decision({}));

    const line = within(list).getByRole("button", { expanded: false });
    expect(line.getAttribute("title")).toBe(SHOWN);
    fireEvent.click(line);

    expect(within(list).getByRole("button", { expanded: true }).textContent).toBe(SHOWN);
    expect(list.textContent).not.toContain("2026-10-13");
  });

  it("reads so on a sentence short enough to need no cut", () => {
    const list = show(decision({ statement: "공지 (기한 2026-10-16)", model_statement: "공지 (기한 2026-10-16)" }));

    expect(within(list).getByText("공지 (기한 10월 16일 금)")).toBeTruthy();
  });

  it("reads so in the line beneath and in the model's sentence", () => {
    const list = show(
      decision({
        statement: "배포는 한 주 미룹니다",
        summary: "배포를 미루기로 했습니다 (기한 2026-10-13)",
      }),
    );

    expect(list.textContent).toContain("배포를 미루기로 했습니다 (기한 10월 13일 화) · 신뢰도 80%");
    expect(list.textContent).toContain(`모델 문장: ${SHOWN}`);
    expect(list.textContent).not.toContain("2026-10-13");
  });

  it("opens the box a sentence is reworded in with the stored sentence, and saves what is typed", () => {
    reword.mockResolvedValue(true);
    const list = show(decision({}));

    fireEvent.click(within(list).getByRole("button", { name: "문장 고치기" }));

    const box = within(list).getByRole("textbox", { name: "결정 문장" }) as HTMLTextAreaElement;
    expect(box.value).toBe(STORED);
    fireEvent.submit(box.closest("form") as HTMLFormElement);
    expect(reword).toHaveBeenCalledWith("dec_1", STORED);
  });

  it("does not call a sentence reworded because it is shown another way", () => {
    const list = show(decision({}));

    expect(list.textContent).not.toContain("모델 문장");
  });

  it("names the decision the same way where it asks before deleting one", () => {
    show(decision({ origin: "user", source_utterance_ids: [] }));

    fireEvent.click(screen.getByRole("button", { name: "삭제" }));

    expect(screen.getByRole("dialog").textContent).toContain(SHOWN);
  });
});
