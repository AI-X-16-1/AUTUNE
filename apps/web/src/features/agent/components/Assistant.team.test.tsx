import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { onAgentActed } from "@/shared/lib/agentActed";

import * as api from "../api";
import type { ChatFinding, ChatReply } from "../types";
import { Assistant } from "./Assistant";

// #1055: off a meeting page a question is about the team chosen in the
// sidebar; each question keeps its team, a line marks the first question about
// another team, report rows open the dashboard card, and the page is told when
// an action ran.

function reply(over: Partial<ChatReply> = {}): ChatReply {
  return {
    run_id: "run_1",
    outcome: "answered",
    route: "report",
    answer: "답입니다.",
    items: [],
    proposed: 0,
    executed: 0,
    queued: 0,
    pending: [],
    ...over,
  };
}

beforeEach(() => {
  // jsdom draws no layout, so it has no scrollIntoView for the message list.
  Element.prototype.scrollIntoView = vi.fn();
  vi.spyOn(api, "listPending").mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

type Team = { id: string; name: string };
const A: Team = { id: "team_a", name: "A팀" };
const B: Team = { id: "team_b", name: "B팀" };

function mount(team: Team, pathname = "/dashboard") {
  const view = render(
    <Assistant
      teamId={team.id}
      teamName={team.name}
      userName="민경"
      pathname={pathname}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /Autune 비서 열기/ }));
  return {
    switchTo: (next: Team) =>
      view.rerender(
        <Assistant
          teamId={next.id}
          teamName={next.name}
          userName="민경"
          pathname={pathname}
        />,
      ),
  };
}

async function ask(text: string): Promise<void> {
  fireEvent.change(screen.getByLabelText("비서에게 물어보기"), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole("button", { name: "보내기" }));
  await waitFor(() =>
    expect(screen.queryByLabelText("답을 기다리는 중")).toBeNull(),
  );
}

describe("Assistant and the chosen team", () => {
  it("asks about the team it is given, and names it in the header", async () => {
    const sent = vi.spyOn(api, "sendChat").mockResolvedValue(reply());

    mount(B);
    await ask("등급 어때?");

    expect(sent).toHaveBeenCalledWith({ teamId: "team_b" }, "등급 어때?");
    expect(screen.getByText("B팀 · 대시보드 보고 있음")).toBeTruthy();
  });

  it("marks the first question about another team, and only once", async () => {
    vi.spyOn(api, "sendChat").mockResolvedValue(reply());

    const { switchTo } = mount(A);
    await ask("첫 질문");
    switchTo(B);
    await ask("두 번째");
    // Back and forth without asking draws nothing more.
    switchTo(A);
    switchTo(B);
    await ask("세 번째");

    const lines = screen.getAllByRole("separator");
    expect(lines.map((l) => l.textContent)).toEqual(["B팀"]);
  });

  it("says which team the next question goes to after a switch", async () => {
    vi.spyOn(api, "sendChat").mockResolvedValue(reply());

    const { switchTo } = mount(A);
    expect(screen.queryByText(/기준으로 답합니다/)).toBeNull();
    await ask("첫 질문");
    switchTo(B);

    expect(screen.getByText("이제 B팀 기준으로 답합니다")).toBeTruthy();
    await ask("두 번째");
    expect(screen.queryByText(/기준으로 답합니다/)).toBeNull();
  });

  it("names only the meeting on a meeting page, where the meeting names the team", async () => {
    vi.spyOn(api, "getMeetingLabel").mockResolvedValue({ title: "주간 회의" });
    const sent = vi.spyOn(api, "sendChat").mockResolvedValue(reply());

    mount(A, "/meetings/mtg_abc123");
    expect(await screen.findByText("주간 회의 보고 있음")).toBeTruthy();
    await ask("이 회의 요약");

    expect(sent).toHaveBeenCalledWith({ meetingId: "mtg_abc123" }, "이 회의 요약");
    expect(screen.queryByRole("separator")).toBeNull();
  });
});

describe("Assistant rows that open something", () => {
  const ROWS: ChatFinding[] = [
    { title: "결제 회의 리포트", body: "", score: 1, meeting_id: "mtg_r1", link: "report" },
    { title: "결제 회의", body: "", score: 1, meeting_id: "mtg_m1" },
    { title: "회의 없는 줄", body: "", score: 1 },
  ];

  it("opens a report row on the dashboard card and any other on the meeting", async () => {
    vi.spyOn(api, "sendChat").mockResolvedValue(reply({ items: ROWS }));

    mount(A, "/actions");
    await ask("리포트 보여줘");

    const href = (name: string) =>
      screen.getByRole("link", { name }).getAttribute("href");
    expect(href("결제 회의 리포트")).toBe("/dashboard#report-mtg_r1");
    expect(href("결제 회의")).toBe("/meetings/mtg_m1");
    expect(screen.queryByRole("link", { name: "회의 없는 줄" })).toBeNull();
  });

  it("on the dashboard a report row only moves the hash", async () => {
    vi.spyOn(api, "sendChat").mockResolvedValue(reply({ items: ROWS }));

    mount(A, "/dashboard");
    await ask("리포트 보여줘");

    const row = screen.getByRole("link", { name: "결제 회의 리포트" });
    expect(row.getAttribute("href")).toBe("#report-mtg_r1");
  });
});

describe("Assistant and the page behind it", () => {
  it("tells the page when an action ran, and not otherwise", async () => {
    const heard = vi.fn();
    const stop = onAgentActed(heard);
    const sent = vi
      .spyOn(api, "sendChat")
      .mockResolvedValueOnce(reply({ executed: 0 }))
      .mockResolvedValueOnce(reply({ executed: 1 }));

    mount(A);
    await ask("등급 어때?");
    expect(heard).not.toHaveBeenCalled();
    await ask("금요일 6시로 바꿔줘");

    expect(sent).toHaveBeenCalledTimes(2);
    expect(heard).toHaveBeenCalledTimes(1);
    stop();
  });
});
