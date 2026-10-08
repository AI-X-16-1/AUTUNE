import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CopyMinutes } from "./CopyMinutes";
import { minutesText, titleOf } from "../minutes";
import type { ActionItemRead, MeetingSummary } from "../types";

// One meeting as a page of minutes on the clipboard (the user, 2026-10-02):
// what was settled and what is left to do, never what was said.

function item(over: Partial<ActionItemRead>): ActionItemRead {
  return {
    id: "act",
    meeting_id: "mtg_1",
    meeting_title: "주간 회의",
    description: "스펙 초안 공유",
    status: "todo",
    confidence: 0.9,
    is_candidate: false,
    origin: "model",
    assignee_id: null,
    assignee_label: null,
    assignee_name: null,
    due_date: null,
    source_utterance_ids: ["utt_1"],
    summary: "제가 금요일까지 스펙 초안 공유하겠습니다",
    ...over,
  } as ActionItemRead;
}

const SUMMARY: MeetingSummary = {
  meeting_id: "mtg_1",
  decisions: [
    { id: "dec_1", statement: "배포는 다음 주 화요일에 한다", status: "confirmed" },
    { id: "dec_2", statement: "검색 개편은 2주 미룬다", status: "pending" },
  ],
  action_items: [
    item({ id: "a1", assignee_name: "김민경", due_date: "2026-10-09", status: "in_progress" }),
    item({ id: "a2", description: "QA 일정 확인", assignee_label: "민구", status: "needs_confirmation" }),
    item({ id: "a3", description: "회고 자료 정리", status: "done" }),
    item({ id: "a4", description: "모델이 확신하지 못한 것", is_candidate: true }),
  ],
  open_questions: 1,
  ambiguous_waiting: 0,
  note: "  다음 회의는 목요일.  ",
  note_updated_at: null,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("minutesText", () => {
  it("lists decisions and actions as the review has them, and the memo", () => {
    expect(minutesText(SUMMARY, titleOf(SUMMARY))).toBe(
      [
        "회의록 — 주간 회의",
        "",
        "결정",
        "- 배포는 다음 주 화요일에 한다",
        "- 검색 개편은 2주 미룬다 (자동 추출)",
        "",
        "액션",
        "- [진행 중] 스펙 초안 공유 — 김민경 · 2026-10-09",
        "- [확인 필요] QA 일정 확인 — 민구 · 기한 없음",
        "- [완료] 회고 자료 정리 — 담당 미지정 · 기한 없음",
        "",
        "팀 메모",
        "다음 회의는 목요일.",
      ].join("\n"),
    );
  });

  it("carries no utterance: not the quotation an item was drawn from", () => {
    const text = minutesText(SUMMARY, titleOf(SUMMARY));

    expect(text).not.toContain("제가 금요일까지 스펙 초안 공유하겠습니다");
    expect(text).not.toContain("utt_1");
  });

  it("leaves a candidate out: a guess is not an outcome", () => {
    expect(minutesText(SUMMARY)).not.toContain("모델이 확신하지 못한 것");
  });

  it("says so when the meeting settled nothing", () => {
    const empty = { ...SUMMARY, decisions: [], action_items: [], note: null };

    expect(minutesText(empty)).toBe(["회의록", "", "결정", "- 없음", "", "액션", "- 없음"].join("\n"));
    expect(titleOf(empty)).toBeNull();
  });
});

describe("CopyMinutes", () => {
  it("puts the page on the clipboard and says what it leaves out", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    render(<CopyMinutes summary={SUMMARY} />);

    expect(screen.getByText(/근거 발화 인용은 넣지 않습니다/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "회의록 복사" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("복사했습니다."));
    expect(writeText).toHaveBeenCalledExactlyOnceWith(minutesText(SUMMARY, "주간 회의"));
  });

  it("shows the page to select by hand when the browser refuses the clipboard", async () => {
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: { writeText: () => Promise.reject(new Error("denied")) },
    });
    render(<CopyMinutes summary={SUMMARY} />);

    fireEvent.click(screen.getByRole("button", { name: "회의록 복사" }));

    const page = (await screen.findByLabelText("회의록")) as HTMLTextAreaElement;
    expect(page.value).toBe(minutesText(SUMMARY, "주간 회의"));
    expect(screen.getByRole("alert").textContent).toContain("직접 선택해 복사해 주세요");
  });
});
