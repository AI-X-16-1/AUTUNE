import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MeetingSummaryScreen } from "./MeetingSummaryScreen";
import { minutesText } from "../minutes";
import type { ActionItemRead, MeetingSummary } from "../types";

// A row's short title on the page of minutes (the user, 2026-10-09): the
// copied text leads a line with it and has nothing else of the row; the 요약
// tab leads with it too and puts the whole sentence under it.

const getSummary = vi.fn<(id: string) => Promise<MeetingSummary>>();
vi.mock("../api", () => ({
  getSummary: (id: string) => getSummary(id),
  putSummaryNote: vi.fn(),
}));

const SENTENCE = "다음 주 수요일까지 결제 화면 오류 목록을 정리해서 디자인 팀에 공유하기";
const TITLE = "결제 화면 오류 목록 공유";
const STATEMENT = "신규 가입 화면은 이메일 인증을 먼저 받고 전화번호 입력은 다음 단계로 옮기기로 함";
const DECIDED = "가입 화면 이메일 인증 먼저";

function item(over: Partial<ActionItemRead>): ActionItemRead {
  return {
    id: "act",
    meeting_id: "mtg_1",
    description: SENTENCE,
    status: "todo",
    is_candidate: false,
    assignee_name: "김민경",
    assignee_label: null,
    due_date: null,
    needs_reassignment: false,
    ...over,
  } as ActionItemRead;
}

const MEETING: MeetingSummary = {
  meeting_id: "mtg_1",
  meeting_title: "주간 회의",
  meeting_started_at: "2026-10-08T03:00:00Z",
  decisions: [
    { id: "dec_1", statement: STATEMENT, title: DECIDED, status: "confirmed" },
    { id: "dec_2", statement: "회의실은 예약제로 한다", title: null, status: "pending" },
  ],
  action_items: [
    item({ id: "a1", title: TITLE }),
    item({ id: "a2", description: "QA 일정 확인", title: null }),
    item({ id: "a3", description: "견적서 보내기", title: "  " }),
  ],
  open_questions: 0,
  ambiguous_waiting: 0,
  note: null,
  note_updated_at: null,
};

afterEach(() => {
  cleanup();
  getSummary.mockReset();
});

describe("a row's short title on the page of minutes", () => {
  it("heads a copied line, and the sentence it stands for is not in the copy", () => {
    const text = minutesText(MEETING, "주간 회의");

    expect(text.split("\n").slice(3)).toEqual([
      "결정 사항",
      `1. ${DECIDED}`,
      "2. 회의실은 예약제로 한다 (자동 추출)",
      "",
      "할 일",
      `1. ${TITLE} — 김민경 · 기한 없음`,
      // No title, or an empty one: the sentence, as before.
      "2. QA 일정 확인 — 김민경 · 기한 없음",
      "3. 견적서 보내기 — 김민경 · 기한 없음",
    ]);
    expect(text).not.toContain(SENTENCE);
    expect(text).not.toContain(STATEMENT);
  });

  it("heads a row of the 요약 tab, with the whole sentence under it", async () => {
    getSummary.mockResolvedValue(MEETING);

    render(<MeetingSummaryScreen meetingId="mtg_1" />);

    const page = await screen.findByRole("article", { name: /^회의록/ });
    const decisions = within(page).getByRole("region", { name: "결정 사항" });
    expect([...decisions.querySelectorAll("li")].map((li) => li.textContent)).toEqual([
      DECIDED + STATEMENT,
      "회의실은 예약제로 한다 (자동 추출)",
    ]);
    // The title first, the sentence after it and on a line of its own.
    const whole = within(decisions).getByText(STATEMENT);
    expect(whole.className).toContain("block");
    expect(whole.previousSibling?.textContent).toBe(DECIDED);

    const actions = within(page).getByRole("region", { name: "할 일" });
    expect([...actions.querySelectorAll("li")].map((li) => li.textContent)).toEqual([
      `${TITLE} — 김민경 · 기한 없음${SENTENCE}`,
      "QA 일정 확인 — 김민경 · 기한 없음",
      "견적서 보내기 — 김민경 · 기한 없음",
    ]);
    expect(within(actions).getByText(SENTENCE).className).toContain("block");
    // A row without a title says its sentence once.
    expect(within(actions).getAllByText("QA 일정 확인")).toHaveLength(1);
  });

  it("shares its top line between the tab and the copy", async () => {
    getSummary.mockResolvedValue(MEETING);

    render(<MeetingSummaryScreen meetingId="mtg_1" />);

    const page = await screen.findByRole("article", { name: /^회의록/ });
    const copied = minutesText(MEETING, "주간 회의");
    for (const li of page.querySelectorAll("section[aria-label='할 일'] li")) {
      const top = li.firstChild?.textContent ?? "";
      expect(copied).toContain(`. ${top} — `);
    }
  });
});
