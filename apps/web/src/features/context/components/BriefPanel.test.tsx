import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { BriefPanel, DRAFT_SHOWN } from "./BriefPanel";
import type { BriefRead } from "../types";

// The agenda draft in the pre-meeting brief (#1147, B3): the brief's last
// section with every entry under the name of where it came from. No switch,
// no model, and the panel is what it was when no draft is asked for.

afterEach(cleanup);

const brief = (overrides: Partial<BriefRead> = {}): BriefRead => ({
  meeting_id: "mtg_next",
  title: "주간 회의",
  starts_at: "2026-10-12T01:00:00Z",
  recap: {
    meeting_id: "mtg_old",
    title: "지난 주간 회의",
    day: "2026-10-05",
    topics: ["검색 개편"],
    decisions: [{ statement: "출시는 11월로 미룹니다", change_type: "modified" }],
  },
  recap_gone: false,
  match_reason: "series",
  agenda: [
    { title: "검색 응답 시간 개선", key: "SRCH-12", status: "진행 중", url: "https://jira.example/SRCH-12" },
  ],
  sent_at: null,
  ...overrides,
});

const GAPS = { label: "지난 회의의 미해결 갭", lines: [{ title: "롤백 계획이 없습니다" }] };
const draft = () => screen.queryByRole("region", { name: "어젠다 초안" });

describe("BriefPanel without a draft", () => {
  it("is the panel it was: the Jira entries under the heading, no draft, no source names", () => {
    render(<BriefPanel brief={brief()} error={null} />);

    expect(draft()).toBeNull();
    expect(screen.getByText("이번 회의에서 다룰 문제")).toBeTruthy();
    expect(screen.getByText("검색 응답 시간 개선")).toBeTruthy();
    expect(screen.queryByText(/어젠다 초안/)).toBeNull();
    expect(screen.queryByText(/열린 Jira 이슈/)).toBeNull();
  });

  it("keeps its own empty line", () => {
    render(<BriefPanel brief={brief({ agenda: [] })} error={null} />);

    expect(screen.getByText("이번 회의에 연결된 안건이 없습니다.")).toBeTruthy();
  });
});

describe("BriefPanel, the agenda draft", () => {
  it("is drawn whenever it is given, with nothing to tick", () => {
    render(<BriefPanel brief={brief()} error={null} carried={[GAPS]} />);

    expect(draft()).not.toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says it is a draft that no model wrote", () => {
    render(<BriefPanel brief={brief()} error={null} carried={[GAPS]} />);

    expect(draft()!.textContent).toContain("어젠다 초안 · 모델을 쓰지 않고 아래 출처에서 그대로 가져왔습니다");
  });

  it("puts every entry under the name of its source: the brief's Jira issues, then the carried ones", () => {
    render(<BriefPanel brief={brief()} error={null} carried={[GAPS]} />);

    const text = draft()!.textContent ?? "";
    expect(text).toContain("열린 Jira 이슈 · 1건");
    expect(text).toContain("지난 회의의 미해결 갭 · 1건");
    expect(text.indexOf("열린 Jira 이슈")).toBeLessThan(text.indexOf("검색 응답 시간 개선"));
    expect(text.indexOf("검색 응답 시간 개선")).toBeLessThan(text.indexOf("지난 회의의 미해결 갭"));
    expect(text.indexOf("지난 회의의 미해결 갭")).toBeLessThan(text.indexOf("롤백 계획이 없습니다"));
  });

  it("draws each Jira entry once, in the draft and nowhere else", () => {
    render(<BriefPanel brief={brief()} error={null} carried={[GAPS]} />);

    expect(screen.getAllByText("검색 응답 시간 개선")).toHaveLength(1);
    expect(screen.getAllByText("이번 회의에서 다룰 문제")).toHaveLength(1);
    // The link to the issue is still the brief's own.
    expect(within(draft()!).getByRole("link", { name: "SRCH-12" })).toBeTruthy();
  });

  it("copies a carried entry as it is: its title and the line under it", () => {
    const carried = [{ label: "끝나지 않은 일", lines: [{ title: "부하 테스트", detail: "지난 주간 회의" }] }];
    render(<BriefPanel brief={brief()} error={null} carried={carried} />);

    expect(within(draft()!).getByText("부하 테스트")).toBeTruthy();
    expect(within(draft()!).getByText("지난 주간 회의")).toBeTruthy();
  });

  it("lists five of a carried source and counts the rest, the count beside the name being of all", () => {
    const many = Array.from({ length: DRAFT_SHOWN + 3 }, (_, index) => ({ title: `갭 ${index}` }));
    render(<BriefPanel brief={brief()} error={null} carried={[{ label: "지난 회의의 미해결 갭", lines: many }]} />);

    expect(draft()!.textContent).toContain("지난 회의의 미해결 갭 · 8건");
    expect(draft()!.textContent).toContain("외 3건");
    // As the source ordered them: the first five, not a choice among them.
    expect(screen.getByText("갭 0")).toBeTruthy();
    expect(screen.getByText("갭 4")).toBeTruthy();
    expect(screen.queryByText("갭 5")).toBeNull();
  });

  it("names no Jira source when the brief has no issue, and still lists what was carried", () => {
    render(<BriefPanel brief={brief({ agenda: [] })} error={null} carried={[GAPS]} />);

    expect(draft()!.textContent).not.toContain("열린 Jira 이슈");
    expect(draft()!.textContent).not.toContain("없습니다.");
    expect(screen.getByText("롤백 계획이 없습니다")).toBeTruthy();
  });

  it("says so when there is nothing to put in it, rather than drawing an empty draft", () => {
    render(<BriefPanel brief={brief({ agenda: [] })} error={null} carried={[]} />);

    expect(draft()!.textContent).toContain("초안에 넣을 것이 아직 없습니다.");
  });

  it("is drawn from the Jira entries alone while the carried sources are still out", () => {
    render(<BriefPanel brief={brief()} error={null} carried={[]} />);

    expect(draft()!.textContent).toContain("열린 Jira 이슈 · 1건");
    expect(draft()!.textContent).not.toContain("초안에 넣을 것이 아직 없습니다.");
  });

  it("leaves the recap above it as it was", () => {
    render(<BriefPanel brief={brief()} error={null} carried={[GAPS]} />);

    expect(screen.getByText("지난 회의")).toBeTruthy();
    expect(screen.getByText("출시는 11월로 미룹니다")).toBeTruthy();
    // The earlier meeting's decisions are the recap's; the draft does not repeat them.
    expect(within(draft()!).queryByText("출시는 11월로 미룹니다")).toBeNull();
  });

  it("is not drawn for a meeting with no brief, whatever was carried", () => {
    const { container } = render(<BriefPanel brief={null} error={null} carried={[GAPS]} />);

    expect(container.textContent).toBe("");
  });
});
