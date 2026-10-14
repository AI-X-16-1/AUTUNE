import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ContextTab } from "./ContextTab";
import { DecisionLineagePanel } from "./DecisionLineagePanel";
import { DecisionTimeline } from "./DecisionTimeline";
import type { DecisionSummaryRead, DecisionVersionRead } from "../types";

// What S22 and the context tab say when they have nothing to show (#1178), and
// where a decision version leads.

const threads = vi.fn<() => DecisionSummaryRead[]>(() => []);
vi.mock("../hooks/useDecisionThreads", () => ({
  useDecisionThreads: () => ({ threads: threads(), loading: false, error: null, reload: vi.fn() }),
}));
vi.mock("../hooks/useDecisionLineage", () => ({
  useDecisionLineage: () => ({ lineage: null, loading: false, error: null }),
}));
vi.mock("../hooks/useTopicLinks", () => ({
  useTopicLinks: () => ({ asserted: [], pending: [], loading: false, error: null, decide: vi.fn() }),
}));
vi.mock("../hooks/useBrief", () => ({ useBrief: () => ({ brief: null, error: null }) }));

afterEach(() => {
  cleanup();
  threads.mockReset();
  threads.mockReturnValue([]);
});

const thread: DecisionSummaryRead = {
  thread_id: "thr_1",
  topic_label: "야간 근무 인원",
  meeting_id: "mtg_2",
  change_type: "modified",
  confidence: 0.8,
  updated_at: "2026-10-05T01:00:00Z",
};

describe("DecisionLineagePanel", () => {
  it("tells a team with no decision yet where decisions come from and how to start", () => {
    render(<DecisionLineagePanel teamId="team_1" />);

    expect(screen.getByText("아직 기록된 결정이 없습니다.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "회의 시작하기" }).getAttribute("href")).toBe(
      "/meetings/new",
    );
    expect(screen.queryByText("왼쪽에서 주제를 선택하세요.")).toBeNull();
  });

  it("asks to pick a topic only when there is one to pick", () => {
    threads.mockReturnValue([thread]);
    render(<DecisionLineagePanel teamId="team_1" />);

    expect(screen.getByText("왼쪽에서 주제를 선택하세요.")).toBeTruthy();
    expect(screen.queryByText("아직 기록된 결정이 없습니다.")).toBeNull();
  });
});

describe("ContextTab", () => {
  it("says how a link comes about when the meeting has none", () => {
    render(<ContextTab meetingId="mtg_1" />);

    expect(screen.getByText("이 회의와 연결된 과거 회의가 없습니다.")).toBeTruthy();
    expect(screen.getByText(/자동으로 연결됩니다/)).toBeTruthy();
  });
});

describe("DecisionTimeline", () => {
  it("links each version to the meeting it was decided in", () => {
    const version: DecisionVersionRead = {
      id: 1,
      meeting_id: "mtg_7",
      current_statement: "야간 근무는 두 명으로 한다",
      previous_statement: null,
      previous_meeting_id: null,
      change_type: "new",
      nli_label: null,
      confidence: 1,
      created_at: "2026-10-05T01:00:00Z",
    };
    render(<DecisionTimeline versions={[version]} />);

    expect(screen.getByRole("link", { name: "결정한 회의 보기" }).getAttribute("href")).toBe(
      "/meetings/mtg_7",
    );
    expect(screen.queryByText(/mtg_7/)).toBeNull();
  });
});
