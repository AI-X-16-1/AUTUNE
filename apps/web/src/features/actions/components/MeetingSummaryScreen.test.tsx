import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MeetingSummaryScreen } from "./MeetingSummaryScreen";
import type { MeetingSummary } from "../types";

// The 요약 tab's v2 section (#421): a model's summary of the meeting, on top
// and labelled as a model's, only when the server sends one.

const getSummary = vi.fn<(id: string) => Promise<MeetingSummary>>();
vi.mock("../api", () => ({
  getSummary: (id: string) => getSummary(id),
  putSummaryNote: vi.fn(),
}));

const BASE: MeetingSummary = {
  meeting_id: "mtg_1",
  decisions: [],
  action_items: [],
  open_questions: 0,
  ambiguous_waiting: 0,
  note: null,
  note_updated_at: null,
};

afterEach(() => {
  cleanup();
  getSummary.mockReset();
});

describe("MeetingSummaryScreen", () => {
  it("shows a written summary first, marked as a model's", async () => {
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

    const section = await screen.findByRole("region", { name: "AI 요약" });
    expect(section.textContent).toContain("배포를 금요일로 미루기로 했습니다.");
    expect(section.textContent).toContain("릴리스 노트는 3시까지 정리합니다");
    expect(section.textContent).toContain("모델이 회의 발화로 쓴 요약입니다");
    const regions = screen
      .getAllByRole("region")
      .map((r) => r.getAttribute("aria-label"));
    expect(regions[0]).toBe("AI 요약");
  });

  it("shows what was said beneath a decision and an item, and nothing beneath a row with none", async () => {
    getSummary.mockResolvedValue({
      ...BASE,
      decisions: [
        { id: "dec_1", statement: "출시를 다음 달로 미룬다", status: "pending", summary: "출시는 다음 달로 미루기로 했습니다." },
        { id: "dec_2", statement: "회의실은 예약제로 한다", status: "confirmed", summary: null },
      ],
      action_items: [
        {
          id: "act_1",
          meeting_id: "mtg_1",
          description: "설문 문항 다시 쓰기",
          status: "todo",
          summary: "설문은 제가 금요일까지 다시 쓰겠습니다.",
        },
      ] as MeetingSummary["action_items"],
    });

    render(<MeetingSummaryScreen meetingId="mtg_1" />);

    const decisions = await screen.findByRole("region", { name: "결정" });
    const rows = decisions.querySelectorAll("li");
    expect(rows[0]?.textContent).toContain("출시를 다음 달로 미룬다");
    expect(rows[0]?.querySelector("p")?.textContent).toBe("출시는 다음 달로 미루기로 했습니다.");
    expect(rows[1]?.querySelector("p")).toBeNull();
    // An unconfirmed decision says where it came from, not that it is waiting.
    expect(rows[0]?.textContent).toContain("출시를 다음 달로 미룬다 · 자동 추출");
    expect(rows[1]?.textContent).not.toContain("자동 추출");
    expect(decisions.textContent).not.toContain("확인 대기");
    const item = screen.getByRole("region", { name: "액션" }).querySelector("li");
    expect(item?.querySelector("p")?.textContent).toBe("설문은 제가 금요일까지 다시 쓰겠습니다.");
  });

  it("has no such section when none is written", async () => {
    getSummary.mockResolvedValue({ ...BASE, generated: null });

    render(<MeetingSummaryScreen meetingId="mtg_1" />);

    await screen.findByRole("region", { name: "개요" });
    expect(screen.queryByRole("region", { name: "AI 요약" })).toBeNull();
  });
});
