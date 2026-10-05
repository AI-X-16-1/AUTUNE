import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectGroups } from "./ProjectGroups";
import type { ActionItemRead, MeetingSummary } from "../types";

// A meeting's decisions and items by project (the user, 2026-10-04), each one
// movable, with the rules run again on request.

const placeDecision = vi.fn<(id: string, to: string | null) => Promise<void>>(
  () => Promise.resolve(),
);
const placeActionItem =
  vi.fn<(id: string, to: string | null) => Promise<ActionItemRead>>();
const assignSummaryProjects = vi.fn<(id: string) => Promise<MeetingSummary>>();
vi.mock("../api", () => ({
  placeDecision: (id: string, to: string | null) => placeDecision(id, to),
  placeActionItem: (id: string, to: string | null) => placeActionItem(id, to),
  assignSummaryProjects: (id: string) => assignSummaryProjects(id),
}));

const item = (over: Partial<ActionItemRead>): ActionItemRead =>
  ({
    id: "act_1",
    meeting_id: "mtg_1",
    description: "로그인 고치기",
    status: "todo",
    confidence: 0.9,
    origin: "model",
    source_utterance_ids: [],
    project_id: "prj_a",
    ...over,
  }) as ActionItemRead;

const SUMMARY: MeetingSummary = {
  meeting_id: "mtg_1",
  decisions: [
    {
      id: "dec_1",
      statement: "배포는 금요일",
      status: "confirmed",
      project_id: "prj_b",
    },
  ],
  action_items: [
    item({}),
    item({ id: "act_2", description: "회의실 예약", project_id: null }),
  ],
  open_questions: 0,
  ambiguous_waiting: 0,
  note: null,
  note_updated_at: null,
  projects: [
    { id: "prj_a", name: "Autune", aliases: [], jira_project_key: null },
    { id: "prj_b", name: "App", aliases: [], jira_project_key: null },
  ],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ProjectGroups", () => {
  it("groups rows under their project, then 미분류", () => {
    render(
      <ProjectGroups meetingId="mtg_1" summary={SUMMARY} onChange={() => {}} />,
    );

    const autune = screen.getByLabelText("Autune");
    const app = screen.getByLabelText("App");
    const unsorted = screen.getByLabelText("미분류");
    expect(autune.textContent).toContain("로그인 고치기");
    expect(app.textContent).toContain("배포는 금요일");
    expect(unsorted.textContent).toContain("회의실 예약");
  });

  it("moves a decision to another project", async () => {
    const onChange = vi.fn<(next: MeetingSummary) => void>();
    render(
      <ProjectGroups meetingId="mtg_1" summary={SUMMARY} onChange={onChange} />,
    );

    fireEvent.change(screen.getByLabelText("배포는 금요일 프로젝트"), {
      target: { value: "prj_a" },
    });

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(placeDecision).toHaveBeenCalledWith("dec_1", "prj_a");
    expect(onChange.mock.calls[0]?.[0].decisions[0]?.project_id).toBe("prj_a");
  });

  it("puts an item in 미분류 as null", async () => {
    placeActionItem.mockResolvedValue(item({ project_id: null }));
    render(
      <ProjectGroups meetingId="mtg_1" summary={SUMMARY} onChange={() => {}} />,
    );

    fireEvent.change(screen.getByLabelText("로그인 고치기 프로젝트"), {
      target: { value: "" },
    });

    await waitFor(() =>
      expect(placeActionItem).toHaveBeenCalledWith("act_1", null),
    );
  });
});
