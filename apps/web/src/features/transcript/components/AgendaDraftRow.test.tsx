import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgendaDraftRow, type AgendaSource } from "./AgendaDraftRow";

// S06's agenda row as the small cut of S08 (#1147): on when a source has
// something for the team. It reports and does not choose, and it draws no
// draft -- the draft is in module D's brief panel.
//
// A status line, not a checkbox (#1147; mkkim68's review of #1193): the row
// is read here as a person hears it -- its name, then 켜짐 or 꺼짐 -- and
// never through a control's role.

afterEach(cleanup);

const ROW = /어젠다 초안 자동 생성/;
const row = () => screen.getByText(ROW).closest("div")!;
/** What the row says of itself in words; the dot beside it is hidden from a screen reader. */
const on = () => within(row()).getByText(/^(켜짐|꺼짐)$/).textContent === "켜짐";
const detail = () => row().textContent ?? "";

type Line = { title: string };
const source = (label: string, lines: Line[] | Error): AgendaSource => ({
  label,
  lines: vi.fn(() => (lines instanceof Error ? Promise.reject(lines) : Promise.resolve(lines))),
});
/** A source whose answer the test lets go of when it chooses. */
function held(label: string) {
  let release: (lines: Line[]) => void = () => undefined;
  const lines = vi.fn(
    () =>
      new Promise<Line[]>((resolve) => {
        release = resolve;
      }),
  );
  return { source: { label, lines } satisfies AgendaSource, release: (l: Line[]) => release(l) };
}

const JIRA = [{ title: "검색 응답 시간 개선" }];
const GAPS = [{ title: "롤백 계획이 없습니다" }];

describe("AgendaDraftRow, when it is on", () => {
  it("is off while the sources are being read, and says so", () => {
    render(<AgendaDraftRow teamId="team_1" sources={[held("열린 Jira 이슈").source]} />);

    expect(on()).toBe(false);
    expect(detail()).toContain("확인하는 중");
  });

  it("is on by one source with a line, though another has none and a third cannot be read", async () => {
    const sources = [
      source("열린 Jira 이슈", []),
      source("읽지 못한 출처", new Error("boom")),
      source("이전 회의의 미해결 갭", GAPS),
    ];
    render(<AgendaDraftRow teamId="team_1" sources={sources} />);

    await waitFor(() => expect(on()).toBe(true));
    // The line under the name says which sources it draws on -- the one that has something.
    expect(detail()).toContain("이전 회의의 미해결 갭에서 모읍니다");
    expect(detail()).not.toContain("Jira");
    expect(detail()).toContain("모델을 쓰지 않습니다");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(detail()).not.toContain("boom");
  });

  it("says where and when the draft appears", async () => {
    render(<AgendaDraftRow teamId="team_1" sources={[source("열린 Jira 이슈", JIRA)]} />);

    await waitFor(() => expect(on()).toBe(true));
    expect(detail()).toContain("“저장만”으로 연 회의는 시작 10분 전 브리프에 초안이 나옵니다");
  });

  it("does not wait for a slow source once another has answered with a line", async () => {
    const slow = held("열린 Jira 이슈");
    render(
      <AgendaDraftRow
        teamId="team_1"
        sources={[slow.source, source("이전 회의의 미해결 갭", GAPS)]}
      />,
    );

    await waitFor(() => expect(on()).toBe(true));
  });

  it("stays off when no source has anything, and names what is missing", async () => {
    const sources = [source("열린 Jira 이슈", []), source("이전 회의의 미해결 갭", [])];
    render(<AgendaDraftRow teamId="team_1" sources={sources} />);

    await waitFor(() => expect(detail()).toContain("아직 아무것도 없습니다"));
    expect(on()).toBe(false);
    expect(detail()).toContain("열린 Jira 이슈, 이전 회의의 미해결 갭");
    expect(detail()).not.toContain("브리프에 초안이 나옵니다");
  });

  it("treats sources that cannot be read as having nothing: off, and no error raised", async () => {
    render(<AgendaDraftRow teamId="team_1" sources={[source("열린 Jira 이슈", new Error("x"))]} />);

    await waitFor(() => expect(detail()).toContain("아직 아무것도 없습니다"));
    expect(on()).toBe(false);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("asks nothing before a team is chosen", () => {
    const jira = source("열린 Jira 이슈", JIRA);
    render(<AgendaDraftRow teamId="" sources={[jira]} />);

    expect(on()).toBe(false);
    expect(detail()).toContain("팀을 고르면");
    expect(jira.lines).not.toHaveBeenCalled();
  });

  it("asks each source once, for the team chosen", async () => {
    const jira = source("열린 Jira 이슈", JIRA);
    render(<AgendaDraftRow teamId="team_1" sources={[jira]} />);

    await waitFor(() => expect(on()).toBe(true));
    expect(jira.lines).toHaveBeenCalledTimes(1);
    expect(jira.lines).toHaveBeenCalledWith("team_1");
  });
});

describe("AgendaDraftRow, what it does not do", () => {
  it("is no checkbox, on or off: a screen reader is told a state, not a control it cannot use", async () => {
    const jira = held("열린 Jira 이슈");
    render(<AgendaDraftRow teamId="team_1" sources={[jira.source]} />);

    // Off, while the source is out.
    expect(on()).toBe(false);
    expect(screen.queryByRole("checkbox")).toBeNull();

    jira.release(JIRA);
    await waitFor(() => expect(on()).toBe(true));
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("shows the same with its dot: a ring while off, filled once on", async () => {
    const jira = held("열린 Jira 이슈");
    render(<AgendaDraftRow teamId="team_1" sources={[jira.source]} />);
    // `StatusDot` draws a ring with a border and a filled dot with none.
    const ring = () => (row().querySelector("[aria-hidden]") as HTMLElement).style.border !== "";

    expect(ring()).toBe(true);

    jira.release(JIRA);
    await waitFor(() => expect(on()).toBe(true));
    expect(ring()).toBe(false);
  });

  it("cannot be changed by hand, on or off: no meeting field would carry the choice", async () => {
    render(<AgendaDraftRow teamId="team_1" sources={[source("열린 Jira 이슈", JIRA)]} />);
    await waitFor(() => expect(on()).toBe(true));

    // Nothing in the row takes a press: no control of any kind, and no label that would pass one on.
    for (const role of ["checkbox", "switch", "button", "radio"]) {
      expect(within(row()).queryByRole(role)).toBeNull();
    }
    expect(row().closest("label")).toBeNull();
    fireEvent.click(row());

    expect(on()).toBe(true);
  });

  it("draws none of the sources' lines: the draft is the brief panel's", async () => {
    render(<AgendaDraftRow teamId="team_1" sources={[source("열린 Jira 이슈", JIRA)]} />);

    await waitFor(() => expect(on()).toBe(true));
    expect(screen.queryByText("검색 응답 시간 개선")).toBeNull();
    expect(screen.queryByRole("region", { name: "어젠다 초안" })).toBeNull();
  });
});

describe("AgendaDraftRow, when the team changes", () => {
  it("asks again for the new team and keeps nothing of the old one's", async () => {
    const lines = vi.fn((teamId: string) => Promise.resolve(teamId === "team_1" ? JIRA : []));
    const sources = [{ label: "열린 Jira 이슈", lines }];
    const view = render(<AgendaDraftRow teamId="team_1" sources={sources} />);
    await waitFor(() => expect(on()).toBe(true));

    view.rerender(<AgendaDraftRow teamId="team_2" sources={sources} />);

    // At once, before the new team has answered: the old team's answer is not this team's.
    expect(on()).toBe(false);
    await waitFor(() => expect(lines).toHaveBeenCalledWith("team_2"));
    await waitFor(() => expect(detail()).toContain("아직 아무것도 없습니다"));
    expect(on()).toBe(false);
  });

  it("is off again with no team chosen, whatever the last team had", async () => {
    const sources = [source("열린 Jira 이슈", JIRA)];
    const view = render(<AgendaDraftRow teamId="team_1" sources={sources} />);
    await waitFor(() => expect(on()).toBe(true));

    view.rerender(<AgendaDraftRow teamId="" sources={sources} />);

    expect(on()).toBe(false);
    expect(detail()).toContain("팀을 고르면");
  });

  it("drops an answer that arrives for the team the person has left", async () => {
    const first = held("열린 Jira 이슈");
    const second = held("열린 Jira 이슈");
    const lines = vi.fn((teamId: string) =>
      teamId === "team_1" ? first.source.lines() : second.source.lines(),
    );
    const sources = [{ label: "열린 Jira 이슈", lines }];
    const view = render(<AgendaDraftRow teamId="team_1" sources={sources} />);

    view.rerender(<AgendaDraftRow teamId="team_2" sources={sources} />);
    await waitFor(() => expect(lines).toHaveBeenCalledWith("team_2"));
    // The old team's answer lands while the new team's is still out.
    first.release(JIRA);
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(on()).toBe(false);
    expect(detail()).toContain("확인하는 중");
  });
});
