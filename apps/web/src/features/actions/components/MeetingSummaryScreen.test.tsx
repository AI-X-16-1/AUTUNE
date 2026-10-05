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

  it("has no such section when none is written", async () => {
    getSummary.mockResolvedValue({ ...BASE, generated: null });

    render(<MeetingSummaryScreen meetingId="mtg_1" />);

    await screen.findByRole("region", { name: "개요" });
    expect(screen.queryByRole("region", { name: "AI 요약" })).toBeNull();
  });
});
