import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { MeetingSummaryScreen } from "./MeetingSummaryScreen";
import { minutesText } from "../minutes";
import type { ActionItemRead, MeetingSummary } from "../types";

// The 요약 tab as the meeting's minutes (the user, 2026-10-09): one document,
// and the page "회의록 복사" copies is the page on screen.

const getSummary = vi.fn<(id: string) => Promise<MeetingSummary>>();
const putSummaryNote = vi.fn<(id: string, text: string) => Promise<MeetingSummary>>();
vi.mock("../api", () => ({
  getSummary: (id: string) => getSummary(id),
  putSummaryNote: (id: string, text: string) => putSummaryNote(id, text),
}));

function item(over: Partial<ActionItemRead>): ActionItemRead {
  return {
    id: "act",
    meeting_id: "mtg_1",
    description: "설문 문항 다시 쓰기",
    status: "todo",
    is_candidate: false,
    assignee_name: null,
    assignee_label: null,
    due_date: null,
    needs_reassignment: false,
    summary: "설문은 제가 금요일까지 다시 쓰겠습니다.",
    ...over,
  } as ActionItemRead;
}

const BASE: MeetingSummary = {
  meeting_id: "mtg_1",
  meeting_title: "10월 2주차 점검",
  meeting_started_at: "2026-10-08T03:00:00Z",
  decisions: [],
  action_items: [],
  open_questions: 0,
  ambiguous_waiting: 0,
  note: null,
  note_updated_at: null,
};

const MEETING: MeetingSummary = {
  ...BASE,
  decisions: [
    { id: "dec_2", statement: "회의실은 예약제로 한다", status: "confirmed", summary: null },
    {
      id: "dec_1",
      statement: "출시를 다음 달로 미룬다",
      status: "pending",
      summary: "출시는 다음 달로 미루기로 했습니다.",
    },
  ],
  action_items: [
    item({ id: "a1", assignee_name: "김민경", due_date: "2999-01-01" }),
    item({ id: "a2", description: "QA 일정 확인", status: "needs_confirmation" }),
    item({ id: "a3", description: "견적서 보내기", due_date: "2020-01-01", status: "in_progress" }),
    item({ id: "a4", description: "모델이 확신하지 못한 것", is_candidate: true }),
  ],
  open_questions: 2,
  ambiguous_waiting: 1,
  note: "다음 회의는 목요일.",
};

afterEach(() => {
  cleanup();
  getSummary.mockReset();
  putSummaryNote.mockReset();
  vi.unstubAllGlobals();
});

async function document_() {
  return screen.findByRole("article", { name: /^회의록/ });
}

describe("MeetingSummaryScreen", () => {
  it("is one document: the meeting and its day, decisions numbered, then actions with who and by when", async () => {
    getSummary.mockResolvedValue(MEETING);

    render(<MeetingSummaryScreen meetingId="mtg_1" />);

    const page = await document_();
    expect(within(page).getByRole("heading", { level: 1 }).textContent).toBe(
      "회의록 — 10월 2주차 점검",
    );
    expect(page.textContent).toContain("2026년 10월 8일 (목)");
    const sections = within(page)
      .getAllByRole("region")
      .map((r) => r.getAttribute("aria-label"));
    expect(sections).toEqual(["결정 사항", "할 일", "메모"]);

    const decisions = within(page).getByRole("region", { name: "결정 사항" });
    expect(decisions.querySelector("ol")).not.toBeNull();
    const settled = [...decisions.querySelectorAll("li")].map((li) => li.textContent);
    // An unconfirmed decision says where it came from, not that it is waiting.
    expect(settled).toEqual(["회의실은 예약제로 한다", "출시를 다음 달로 미룬다 (자동 추출)"]);
    expect(decisions.textContent).not.toContain("확인 대기");

    const actions = within(page).getByRole("region", { name: "할 일" });
    expect([...actions.querySelectorAll("li")].map((li) => li.textContent)).toEqual([
      "설문 문항 다시 쓰기 — 김민경 · 2999년 1월 1일 화",
      "QA 일정 확인 — 담당 미지정 · 기한 없음 · 확인 필요",
      "견적서 보내기 — 담당 미지정 · 2020년 1월 1일 수 · 진행 중 · 기한 지남",
    ]);
  });

  it("writes a decision's deadline as the action lines write theirs", async () => {
    getSummary.mockResolvedValue({
      ...MEETING,
      meeting_started_at: "2026-10-08T03:00:00Z",
      decisions: [
        { id: "dec_3", statement: "배포는 미룹니다 (기한 2026-10-13)", status: "confirmed", summary: null },
      ],
    });

    render(<MeetingSummaryScreen meetingId="mtg_1" />);

    const decisions = within(await document_()).getByRole("region", { name: "결정 사항" });
    expect([...decisions.querySelectorAll("li")].map((li) => li.textContent)).toEqual([
      "배포는 미룹니다 (기한 10월 13일 화)",
    ]);
  });

  it("shows nothing the copy would not carry: no line of what was said, no candidate", async () => {
    getSummary.mockResolvedValue(MEETING);

    render(<MeetingSummaryScreen meetingId="mtg_1" />);

    const page = await document_();
    expect(page.textContent).not.toContain("출시는 다음 달로 미루기로 했습니다.");
    expect(page.textContent).not.toContain("설문은 제가 금요일까지 다시 쓰겠습니다.");
    expect(page.textContent).not.toContain("모델이 확신하지 못한 것");
    // What the page leaves out is said beside it, not on it.
    const left = screen.getByRole("region", { name: "회의록에 없는 것" });
    expect(left.textContent).toContain(
      "후보 1건 · 미결 질문 2건 · 응답 대기 모호 동의 1건은 회의록에 넣지 않았습니다.",
    );
    expect(within(left).getByRole("link").getAttribute("href")).toBe("/meetings/mtg_1/actions");
  });

  it("copies the page that is on screen", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    getSummary.mockResolvedValue(MEETING);

    render(<MeetingSummaryScreen meetingId="mtg_1" />);

    const page = await document_();
    fireEvent.click(screen.getByRole("button", { name: "회의록 복사" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledOnce());
    const copied = writeText.mock.calls[0]?.[0] as string;
    expect(copied).toBe(minutesText(MEETING, "10월 2주차 점검"));
    // Every line of the copy is on the page; a list number is the list's.
    for (const line of copied.split("\n").filter(Boolean)) {
      expect(page.textContent).toContain(line.replace(/^\d+\. /, ""));
    }
  });

  it("shows one project's minutes when one is chosen beside the copy button, and copies those", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    getSummary.mockResolvedValue({
      ...BASE,
      decisions: [
        { id: "d1", statement: "결정 A", status: "confirmed", project_id: "prj_a" },
        { id: "d2", statement: "결정 B", status: "confirmed", project_id: "prj_b" },
      ],
      action_items: [
        item({ id: "a1", description: "할 일 A", project_id: "prj_a" }),
        item({ id: "a2", description: "할 일 밖", project_id: null }),
      ],
      projects: [
        { id: "prj_a", name: "Autune", aliases: [], jira_project_key: null },
        { id: "prj_b", name: "App", aliases: [], jira_project_key: null },
      ],
    } as MeetingSummary);

    render(<MeetingSummaryScreen meetingId="mtg_1" />);

    const page = await document_();
    expect(page.textContent).toContain("결정 B");
    fireEvent.change(screen.getByLabelText("프로젝트로 거르기"), {
      target: { value: "prj_a" },
    });

    expect(within(page).getByRole("heading", { level: 1 }).textContent).toBe(
      "회의록 — 10월 2주차 점검 · Autune",
    );
    expect(page.textContent).toContain("결정 A");
    expect(page.textContent).not.toContain("결정 B");
    expect(page.textContent).toContain("할 일 A");
    expect(page.textContent).not.toContain("할 일 밖");

    fireEvent.click(screen.getByRole("button", { name: "회의록 복사" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledOnce());
    const copied = writeText.mock.calls[0]?.[0] as string;
    expect(copied.startsWith("회의록 — 10월 2주차 점검 · Autune")).toBe(true);
    expect(copied).not.toContain("결정 B");
  });

  it("puts a written summary first in the document, marked as a model's", async () => {
    getSummary.mockResolvedValue({
      ...BASE,
      generated: {
        overview: "배포를 금요일로 미루기로 했습니다.",
        points: ["릴리스 노트는 3시까지 정리합니다"],
        model_version: "llm:first",
        created_at: "2026-10-04T03:00:00Z",
      },
    });

    render(<MeetingSummaryScreen meetingId="mtg_1" />);

    const page = await document_();
    const section = within(page).getByRole("region", { name: "요약" });
    expect(within(section).getByRole("heading").textContent).toBe("요약 · AI 작성");
    expect(section.textContent).toContain("배포를 금요일로 미루기로 했습니다.");
    expect(section.textContent).toContain("릴리스 노트는 3시까지 정리합니다");
    expect(section.textContent).toContain("모델이 회의 발화로 쓴 요약입니다");
    expect(within(page).getAllByRole("region")[0]).toBe(section);
  });

  it("says beside the document when the meeting was too long for a summary", async () => {
    getSummary.mockResolvedValue({ ...BASE, generated: null, generated_too_long: true });

    render(<MeetingSummaryScreen meetingId="mtg_1" />);

    const page = await document_();
    expect(within(page).queryByRole("region", { name: "요약" })).toBeNull();
    expect(page.textContent).not.toContain("AI 요약");
    expect(screen.getByRole("region", { name: "회의록에 없는 것" }).textContent).toContain(
      "회의가 길어 AI 요약을 만들지 못했습니다",
    );
  });

  it("has no summary section when none is written, and says 없음 where nothing was settled", async () => {
    getSummary.mockResolvedValue({ ...BASE, generated: null });

    render(<MeetingSummaryScreen meetingId="mtg_1" />);

    const page = await document_();
    expect(within(page).queryByRole("region", { name: "요약" })).toBeNull();
    expect(within(page).getByRole("region", { name: "결정 사항" }).textContent).toBe(
      "결정 사항없음",
    );
    expect(within(page).getByRole("region", { name: "할 일" }).textContent).toBe("할 일없음");
    expect(screen.getByRole("region", { name: "회의록에 없는 것" }).textContent).not.toContain(
      "넣지 않았습니다",
    );
  });
});

describe("MeetingSummaryScreen, the memo", () => {
  const memo = () => screen.getByRole("textbox", { name: "메모" }) as HTMLTextAreaElement;
  const save = () =>
    fireEvent.click(
      within(screen.getByRole("region", { name: "메모" })).getByRole("button", { name: "저장" }),
    );

  it("says which kind of value to take out of a memo refused as personal data (#1130)", async () => {
    getSummary.mockResolvedValue(BASE);
    putSummaryNote.mockRejectedValue(
      new ApiError(422, "validation_error", "this text looks like it holds personal data", {
        field: "body",
        reason: "personal_data",
        categories: ["email"],
      }),
    );
    render(<MeetingSummaryScreen meetingId="mtg_1" />);
    await document_();

    fireEvent.change(memo(), { target: { value: "문의는 kim@example.com 으로" } });
    save();

    const section = screen.getByRole("region", { name: "메모" });
    await waitFor(() =>
      expect(section.textContent).toContain(
        "이메일 주소로 보이는 값이 있어 저장하지 않았습니다. 그 값을 지우고 다시 저장해 주세요.",
      ),
    );
    expect(memo().value).toBe("문의는 kim@example.com 으로");
  });

  it("says only that the save failed for any other failure", async () => {
    getSummary.mockResolvedValue(BASE);
    putSummaryNote.mockRejectedValue(new ApiError(500, "internal", "boom"));
    render(<MeetingSummaryScreen meetingId="mtg_1" />);
    await document_();

    fireEvent.change(memo(), { target: { value: "다음 회의는 금요일." } });
    save();

    const section = screen.getByRole("region", { name: "메모" });
    await waitFor(() =>
      expect(section.textContent).toContain("저장하지 못했습니다. 다시 시도해 주세요."),
    );
    expect(section.textContent).not.toContain("boom");
  });
});
