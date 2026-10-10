import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEMO_COMPARISON, DEMO_EXPLANATIONS, DEMO_REPORT } from "../fixtures/report-demo";
import type { GapReport, TemplateComparison } from "../types";
import { GapReportScreen } from "./GapReportScreen";

// S20's grey buttons say why (#1177), and a report not yet read blocks
// "질문 카드 Slack 전송": the server would post gaps nobody on the screen saw
// (review of #1182).

const report = vi.fn<() => Promise<GapReport>>();
const comparison = vi.fn<() => Promise<TemplateComparison>>();
vi.mock("../api", () => ({
  getReport: () => report(),
  getTemplateComparison: () => comparison(),
  getExplanations: () => Promise.resolve(DEMO_EXPLANATIONS),
  listTemplates: () => Promise.resolve([]),
  getAgendaEvents: vi.fn(),
  getAskTargets: vi.fn(),
  askGap: vi.fn(),
  carryMeeting: vi.fn(),
  chooseTemplate: vi.fn(),
  dismissGap: vi.fn(),
  editQuestion: vi.fn(),
  sendCards: vi.fn(),
  undoDismissGap: vi.fn(),
}));

beforeEach(() => {
  comparison.mockResolvedValue(DEMO_COMPARISON);
});

afterEach(() => {
  cleanup();
  report.mockReset();
  comparison.mockReset();
});

const slack = () => screen.getByRole("button", { name: "질문 카드 Slack 전송" }) as HTMLButtonElement;
const reason = (button: HTMLElement) =>
  document.getElementById(button.getAttribute("aria-describedby") ?? "")?.textContent;

describe("질문 카드 Slack 전송 — when it can be pressed", () => {
  it("waits for the report, and says so", () => {
    report.mockReturnValue(new Promise(() => {}));
    render(<GapReportScreen meetingId="mtg_demo" />);

    expect(slack().disabled).toBe(true);
    expect(reason(slack())).toBe("갭을 불러오는 중입니다");
  });

  it("stays blocked when the report never loaded", async () => {
    report.mockRejectedValue(new Error("500"));
    render(<GapReportScreen meetingId="mtg_demo" />);

    await waitFor(() => expect(reason(slack())).toBe("갭을 불러오지 못했습니다"));
    expect(slack().disabled).toBe(true);
  });

  it("is offered once a report with a high gap is on the screen", async () => {
    report.mockResolvedValue(DEMO_REPORT);
    render(<GapReportScreen meetingId="mtg_demo" />);

    await waitFor(() => expect(slack().disabled).toBe(false));
    expect(slack().getAttribute("aria-describedby")).toBeNull();
  });

  it("says there is nothing high to send", async () => {
    report.mockResolvedValue({
      ...DEMO_REPORT,
      gaps: (DEMO_REPORT.gaps ?? []).filter((gap) => gap.severity !== "high"),
    });
    render(<GapReportScreen meetingId="mtg_demo" />);

    await waitFor(() => expect(reason(slack())).toBe("보낼 위험도 높은 갭이 없습니다"));
    expect(slack().disabled).toBe(true);
  });
});

describe("verdict chips", () => {
  it("split a compared meeting's gaps", async () => {
    report.mockResolvedValue(DEMO_REPORT);
    render(<GapReportScreen meetingId="mtg_demo" />);

    expect(await screen.findByRole("group", { name: "판정" })).toBeTruthy();
  });

  it("are not drawn for a meeting not yet compared", async () => {
    report.mockResolvedValue(DEMO_REPORT);
    comparison.mockResolvedValue({ ...DEMO_COMPARISON, analysed: false });
    render(<GapReportScreen meetingId="mtg_demo" />);

    // The rail has drawn the comparison, so the screen has what it splits by.
    await screen.findByText("아직 대조하지 않았습니다");
    await screen.findByText("이 회의에서 빠진 논의");
    expect(screen.queryByRole("group", { name: "판정" })).toBeNull();
  });
});
