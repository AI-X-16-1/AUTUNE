import { afterEach, describe, expect, it, vi } from "vitest";

import { EARLIER_GAPS_AGENDA, OPEN_GAPS_AGENDA } from "./agendaSource";
import type { GapReport, GapSeverity, TeamGap } from "./types";

// Module C's two sources for an agenda draft (#1147): the team's open gaps
// for the new-meeting form's row, an earlier meeting's for the brief.

const list = vi.fn<(teamId: string, severities: readonly GapSeverity[]) => Promise<TeamGap[]>>();
const report = vi.fn<(meetingId: string) => Promise<GapReport>>();
vi.mock("./api", () => ({
  getReport: (meetingId: string) => report(meetingId),
  listTeamGaps: (teamId: string, severities: readonly GapSeverity[]) => list(teamId, severities),
}));

afterEach(() => {
  list.mockReset();
  report.mockReset();
});

const gap = (overrides: Partial<TeamGap>): TeamGap => ({
  gap_id: "gap_1",
  meeting_id: "mtg_old",
  meeting_title: "주간 회의",
  meeting_date: "2026-10-02T09:00:00Z",
  category: "technical_spec",
  title: "롤백 계획이 없습니다",
  severity: "high",
  risk_score: 0.9,
  template_item: null,
  suggested_question: "롤백은 누가 결정합니까?",
  ...overrides,
});

describe("OPEN_GAPS_AGENDA", () => {
  it("reads the existing team list, HIGH alone", async () => {
    list.mockResolvedValue([]);

    await OPEN_GAPS_AGENDA.lines("team_1");

    expect(list).toHaveBeenCalledWith("team_1", ["high"]);
  });

  it("is each gap's title with the meeting it came from, in the list's order", async () => {
    list.mockResolvedValue([gap({}), gap({ gap_id: "gap_2", title: "성능 목표가 없습니다" })]);

    expect(await OPEN_GAPS_AGENDA.lines("team_1")).toEqual([
      { title: "롤백 계획이 없습니다", detail: "주간 회의 · 2026-10-02" },
      { title: "성능 목표가 없습니다", detail: "주간 회의 · 2026-10-02" },
    ]);
  });

  it("is named for what the list is, not for gaps sent on to the next meeting", () => {
    expect(OPEN_GAPS_AGENDA.label).toBe("이전 회의의 미해결 갭");
    expect(OPEN_GAPS_AGENDA.label).not.toContain("넘어온");
  });
});

describe("EARLIER_GAPS_AGENDA", () => {
  const found = (id: string, title: string, severity: GapSeverity) => ({
    id,
    category: "technical_spec" as const,
    title,
    severity,
    risk_score: 0.5,
  });

  it("reads the report of the meeting it is asked about, which leaves out what was dismissed", async () => {
    report.mockResolvedValue({ meeting_id: "mtg_old", gaps: [] });

    await EARLIER_GAPS_AGENDA.lines("mtg_old");

    expect(report).toHaveBeenCalledWith("mtg_old");
    expect(list).not.toHaveBeenCalled();
  });

  it("is each gap's title alone, every severity, in the report's order", async () => {
    report.mockResolvedValue({
      meeting_id: "mtg_old",
      gaps: [
        found("gap_1", "롤백 계획이 없습니다", "high"),
        found("gap_2", "성능 목표가 없습니다", "medium"),
      ],
    });

    expect(await EARLIER_GAPS_AGENDA.lines("mtg_old")).toEqual([
      { title: "롤백 계획이 없습니다" },
      { title: "성능 목표가 없습니다" },
    ]);
  });

  it("is nothing for a report that carries no gap list", async () => {
    report.mockResolvedValue({ meeting_id: "mtg_old" });

    expect(await EARLIER_GAPS_AGENDA.lines("mtg_old")).toEqual([]);
  });

  it("is named for what the list is", () => {
    expect(EARLIER_GAPS_AGENDA.label).toBe("지난 회의의 미해결 갭");
    expect(EARLIER_GAPS_AGENDA.label).not.toContain("넘어온");
  });
});
