import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { ActionDetailDrawer } from "./ActionDetailDrawer";
import { AddActionItem } from "./AddActionItem";
import { DecisionReview } from "./DecisionReview";
import { ProjectSettings } from "./ProjectSettings";
import type { ActionItemRead, MeetingReview, Project, ReviewDecision } from "../types";

// Text a person typed is screened when it is saved, and a save that holds what
// reads as personal data is refused (#1130). Each form says which kind of value
// to take out, in the screen's language, and keeps what was typed -- the
// server's English sentence is not what the person reads. The memo, the
// materials shelf and the name suggestions have theirs beside their own tests.

const TYPED = "거래처 010-1234-5678 로 견적 요청";
const SAID = "전화번호로 보이는 값이 있어 저장하지 않았습니다. 그 값을 지우고 다시 저장해 주세요.";
const refusal = (field: string) =>
  new ApiError(
    422,
    "validation_error",
    "this text looks like it holds personal data and was not saved; take that value out and save again",
    { field, reason: "personal_data", categories: ["phone"] },
  );

const reword = vi.fn<(id: string, statement: string) => Promise<void>>();
const add = vi.fn<(statement: string) => Promise<void>>();
const review = vi.fn<() => MeetingReview>();
vi.mock("../hooks/useDecisionReview", () => ({
  useDecisionReview: () => ({
    review: review(),
    loading: false,
    error: null,
    setStatus: vi.fn(),
    reword: (id: string, statement: string) => reword(id, statement),
    add: (statement: string) => add(statement),
    remove: vi.fn(),
  }),
}));
vi.mock("../hooks/useDecisionSources", () => ({ useDecisionSources: () => ({}) }));
vi.mock("../hooks/useSourceUtterances", () => ({
  useSourceUtterances: () => ({ status: "loading" }),
}));
// No member list: the assignee is a name typed by hand, which is the text screened.
vi.mock("../hooks/useAssignable", () => ({ useAssignable: () => null }));

const createProject = vi.fn<() => Promise<Project>>();
vi.mock("../api", async (original) => ({
  ...(await original<typeof import("../api")>()),
  listProjects: () => Promise.resolve([]),
  listProjectSuggestions: () => Promise.resolve([]),
  createProject: () => createProject(),
}));

const DECISION: ReviewDecision = {
  id: "dec_1",
  statement: "배포는 금요일에 한다",
  model_statement: "배포는 금요일에 한다",
  confidence: 0.8,
  origin: "model",
  needs_recheck: false,
  status: "pending",
  suggested: null,
  source_utterance_ids: [],
  summary: null,
};

const shown = async () => (await screen.findByRole("alert")).textContent;
const set = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("a save refused as personal data, on each of module B's forms", () => {
  it("a rewording: the editor stays open with what was typed", async () => {
    review.mockReturnValue({
      meeting_id: "mtg_1",
      decisions: [DECISION],
      ambiguous_agreements: [],
      action_items: [],
      pending_decisions: 1,
    });
    reword.mockRejectedValue(refusal("statement"));
    render(<DecisionReview meetingId="mtg_1" />);

    fireEvent.click(screen.getByRole("button", { name: "문장 고치기" }));
    set("결정 문장", TYPED);
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    expect(await shown()).toBe(SAID);
    expect((screen.getByLabelText("결정 문장") as HTMLTextAreaElement).value).toBe(TYPED);
  });

  it("a decision typed by hand", async () => {
    review.mockReturnValue({
      meeting_id: "mtg_1",
      decisions: [],
      ambiguous_agreements: [],
      action_items: [],
      pending_decisions: 0,
    });
    add.mockRejectedValue(refusal("statement"));
    render(<DecisionReview meetingId="mtg_1" />);

    fireEvent.click(screen.getByRole("button", { name: "+ 결정 추가" }));
    set("추가할 결정", TYPED);
    fireEvent.click(screen.getByRole("button", { name: "추가" }));

    expect(await shown()).toBe(SAID);
    expect((screen.getByLabelText("추가할 결정") as HTMLTextAreaElement).value).toBe(TYPED);
  });

  it("an action item typed by hand", async () => {
    const onAdd = vi.fn(() => Promise.reject(refusal("description")));
    render(<AddActionItem meetingId="mtg_1" onAdd={onAdd} />);

    fireEvent.click(screen.getByRole("button", { name: /추가/ }));
    set("할 일", TYPED);
    fireEvent.click(screen.getByRole("button", { name: "추가" }));

    expect(await shown()).toBe(SAID);
    expect((screen.getByLabelText("할 일") as HTMLTextAreaElement).value).toBe(TYPED);
  });

  it("an assignee's name typed in the detail window", async () => {
    const item = {
      id: "a",
      meeting_id: "mtg_1",
      description: "배포 일정 공유",
      status: "todo",
      confidence: 0.92,
      is_candidate: false,
    } as ActionItemRead;
    const onAssigneeChange = vi.fn(() => Promise.reject(refusal("assignee_label")));
    render(
      <ActionDetailDrawer item={item} onClose={() => {}} onAssigneeChange={onAssigneeChange} />,
    );

    set("담당", "김민수 010-1234-5678");
    fireEvent.click(screen.getByRole("button", { name: "이름 저장" }));

    expect(await shown()).toBe(SAID);
    expect((screen.getByLabelText("담당") as HTMLInputElement).value).toBe(
      "김민수 010-1234-5678",
    );
  });

  it("a project's name", async () => {
    createProject.mockRejectedValue(refusal("name"));
    render(<ProjectSettings teamId="team_a" />);

    fireEvent.change(await screen.findByRole("textbox", { name: "프로젝트 이름" }), {
      target: { value: "문의 010-1234-5678" },
    });
    fireEvent.click(screen.getByRole("button", { name: "추가" }));

    expect((await screen.findByRole("status")).textContent).toBe(SAID);
    expect((screen.getByRole("textbox", { name: "프로젝트 이름" }) as HTMLInputElement).value).toBe(
      "문의 010-1234-5678",
    );
  });
});
