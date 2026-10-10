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
  it("does not call an item closed without being finished 완료 (#856)", () => {
    const text = minutesText({
      ...SUMMARY,
      action_items: [
        item({ id: "a9", description: "외주 견적 받기", status: "done", closed_unfinished: true }),
        item({ id: "a10", description: "회고 자료 정리", status: "done" }),
      ],
    });

    expect(text).toContain("1. 외주 견적 받기 — 담당 미지정 · 기한 없음 · 끝내지 않고 닫힘");
    expect(text).toContain("2. 회고 자료 정리 — 담당 미지정 · 기한 없음 · 완료");
  });

  it("lists decisions and actions as the review has them, and the memo", () => {
    expect(minutesText(SUMMARY, titleOf(SUMMARY))).toBe(
      [
        "회의록 — 주간 회의",
        "",
        "결정 사항",
        "1. 배포는 다음 주 화요일에 한다",
        "2. 검색 개편은 2주 미룬다 (자동 추출)",
        "",
        "할 일",
        // No date line on this page, so the due date says its own year.
        // The state after a dot like every other part, not in brackets: a due
        // date already ends in them.
        "1. 스펙 초안 공유 — 김민경 · 2026년 10월 9일 금 · 진행 중",
        "2. QA 일정 확인 — 민구 · 기한 없음 · 확인 필요",
        "3. 회고 자료 정리 — 담당 미지정 · 기한 없음 · 완료",
        "",
        "메모",
        "다음 회의는 목요일.",
      ].join("\n"),
    );
  });

  it("is headed with the meeting's own title and day, and says nothing after an item not begun", () => {
    const text = minutesText(
      {
        ...SUMMARY,
        meeting_title: "10월 2주차 점검",
        meeting_started_at: "2026-10-08T03:00:00Z",
        decisions: [],
        action_items: [item({ id: "a5", assignee_name: "박재경", status: "todo" })],
        note: null,
      },
      "10월 2주차 점검",
    );

    expect(text.split("\n").slice(0, 2)).toEqual([
      "회의록 — 10월 2주차 점검",
      "2026년 10월 8일 (목)",
    ]);
    expect(text).toContain("1. 스펙 초안 공유 — 박재경 · 기한 없음");
    expect(text).not.toContain("진행 전");
  });

  it("writes a due date as the date line writes a day, and leaves the year to that line", () => {
    const text = minutesText({
      ...SUMMARY,
      meeting_started_at: "2026-10-08T03:00:00Z",
      action_items: [
        item({ id: "a6", due_date: "2026-10-13", status: "todo" }),
        item({ id: "a7", description: "내년 예산안 내기", due_date: "2027-01-05", status: "todo" }),
      ],
    });

    expect(text).toContain("2026년 10월 8일 (목)");
    expect(text).toContain("1. 스펙 초안 공유 — 담당 미지정 · 10월 13일 화\n");
    // Another year than the meeting's: the date line would give the wrong one.
    expect(text).toContain("2. 내년 예산안 내기 — 담당 미지정 · 2027년 1월 5일 화\n");
    expect(text).not.toContain("2026-10-13");
  });

  it("writes a decision's deadline as it writes an action's, and leaves the year to the date line", () => {
    const text = minutesText({
      ...SUMMARY,
      meeting_started_at: "2026-10-08T03:00:00Z",
      decisions: [
        { id: "dec_3", statement: "배포는 미룹니다 (담당 박지영, 기한 2026-10-13)", status: "confirmed" },
        { id: "dec_4", statement: "예산안은 새해에 냅니다 (기한 2027-01-05)", status: "pending" },
        { id: "dec_5", statement: "2026-10-20에 다시 봅니다", status: "confirmed" },
      ],
      action_items: [item({ id: "a6", due_date: "2026-10-13", status: "todo" })],
    } as MeetingSummary);

    expect(text).toContain("1. 배포는 미룹니다 (담당 박지영, 기한 10월 13일 화)\n");
    expect(text).toContain("2. 예산안은 새해에 냅니다 (기한 2027년 1월 5일 화) (자동 추출)\n");
    // A date somebody said is theirs; only the deadline the server appended is rewritten.
    expect(text).toContain("3. 2026-10-20에 다시 봅니다\n");
    expect(text).not.toContain("2026-10-13");
  });

  it("says a decision's year where the minutes have no date line to say it", () => {
    const text = minutesText({
      ...SUMMARY,
      decisions: [{ id: "dec_3", statement: "배포는 미룹니다 (기한 2026-10-13)", status: "confirmed" }],
    } as MeetingSummary);

    expect(text).toContain("1. 배포는 미룹니다 (기한 2026년 10월 13일 화)\n");
  });

  it("shows a due date it cannot read as it came", () => {
    const text = minutesText({
      ...SUMMARY,
      meeting_started_at: "2026-10-08T03:00:00Z",
      action_items: [item({ id: "a8", due_date: "2026-10", status: "todo" })],
    });

    expect(text).toContain("1. 스펙 초안 공유 — 담당 미지정 · 2026-10\n");
  });

  it("puts a model's summary on top, under a heading that says a model wrote it", () => {
    const text = minutesText({
      ...SUMMARY,
      generated: {
        overview: "배포를 다음 주로 미루기로 했습니다.",
        points: ["QA 일정은 민구가 확인합니다"],
        model_version: "llm:first",
        created_at: "2026-10-08T03:30:00Z",
      },
    });

    expect(text.split("\n").slice(0, 5)).toEqual([
      "회의록",
      "",
      "요약 (AI 작성)",
      "배포를 다음 주로 미루기로 했습니다.",
      "- QA 일정은 민구가 확인합니다",
    ]);
  });

  it("names the meeting by its own title before a row's", () => {
    expect(titleOf({ ...SUMMARY, meeting_title: "10월 2주차 점검" })).toBe("10월 2주차 점검");
    expect(titleOf(SUMMARY)).toBe("주간 회의");
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

    expect(minutesText(empty)).toBe(
      ["회의록", "", "결정 사항", "없음", "", "할 일", "없음"].join("\n"),
    );
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
