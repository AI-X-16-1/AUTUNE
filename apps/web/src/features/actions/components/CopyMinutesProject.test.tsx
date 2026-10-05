import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CopyMinutes } from "./CopyMinutes";
import type { ActionItemRead, MeetingSummary } from "../types";

// "회의록 복사" for one project (the user, 2026-10-04).

afterEach(cleanup);

const item = (id: string, project: string | null): ActionItemRead =>
  ({
    id,
    meeting_id: "mtg_1",
    meeting_title: "주간 회의",
    description: `할 일 ${id}`,
    status: "todo",
    confidence: 0.9,
    is_candidate: false,
    origin: "model",
    source_utterance_ids: [],
    project_id: project,
  }) as unknown as ActionItemRead;

const SUMMARY: MeetingSummary = {
  meeting_id: "mtg_1",
  decisions: [
    { id: "d1", statement: "결정 A", status: "confirmed", project_id: "prj_a" },
    { id: "d2", statement: "결정 B", status: "confirmed", project_id: "prj_b" },
  ],
  action_items: [item("1", "prj_a"), item("2", null)],
  open_questions: 0,
  ambiguous_waiting: 0,
  note: null,
  note_updated_at: null,
  projects: [
    { id: "prj_a", name: "Autune", aliases: [], jira_project_key: null },
    { id: "prj_b", name: "App", aliases: [], jira_project_key: null },
  ],
};

describe("CopyMinutes, one project", () => {
  it("copies only the chosen project's rows, titled with it", async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>(() =>
      Promise.resolve(),
    );
    Object.assign(navigator, { clipboard: { writeText } });
    render(<CopyMinutes summary={SUMMARY} />);

    fireEvent.change(screen.getByLabelText("프로젝트로 거르기"), {
      target: { value: "prj_a" },
    });
    fireEvent.click(screen.getByRole("button", { name: "회의록 복사" }));

    await screen.findByText("복사했습니다.");
    const text = writeText.mock.calls[0]?.[0] ?? "";
    expect(text.startsWith("회의록 — 주간 회의 · Autune")).toBe(true);
    expect(text).toContain("결정 A");
    expect(text).not.toContain("결정 B");
    expect(text).toContain("할 일 1");
    expect(text).not.toContain("할 일 2");
  });
});
