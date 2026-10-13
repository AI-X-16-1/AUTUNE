import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { resetPendingCounts } from "../pendingCount";
import type { PendingAction } from "../types";

type PendingStatus = PendingAction["status"];
import { Assistant } from "./Assistant";

// S34 header: "{meeting title} 보고 있음" on a meeting page, the team and the
// page's name elsewhere (docs/design/agent-assistant.md 3.1, #1055).

afterEach(() => {
  cleanup();
  resetPendingCounts();
  vi.restoreAllMocks();
});

function open(pathname: string): void {
  render(
    <Assistant
      teamId="team_1"
      teamName="A팀"
      userName="민경"
      pathname={pathname}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /Autune 비서 열기/ }));
}

describe("Assistant header", () => {
  it("names the meeting on a meeting page", async () => {
    vi.spyOn(api, "getMeetingLabel").mockResolvedValue({ title: "주간 회의" });

    open("/meetings/mtg_abc123");

    expect(await screen.findByText("주간 회의 보고 있음")).toBeTruthy();
    expect(api.getMeetingLabel).toHaveBeenCalledWith("mtg_abc123");
  });

  it("keeps the page name when the title cannot be read", async () => {
    vi.spyOn(api, "getMeetingLabel").mockRejectedValue(new Error("404"));

    open("/meetings/mtg_abc123");

    expect(await screen.findByText("회의 보고 있음")).toBeTruthy();
  });

  it("asks for no title off a meeting page", () => {
    const label = vi.spyOn(api, "getMeetingLabel");

    open("/actions");

    expect(screen.getByText("A팀 · 할 일 보고 있음")).toBeTruthy();
    expect(label).not.toHaveBeenCalled();
  });
});

describe("Assistant when the model is busy", () => {
  it("says to try again shortly", async () => {
    // jsdom draws no layout, so it has no scrollIntoView for the message list.
    Element.prototype.scrollIntoView = vi.fn();
    const { ApiError } = await import("@/shared/api/client");
    vi.spyOn(api, "sendChat").mockRejectedValue(
      new ApiError(503, "agent_busy", "busy"),
    );

    open("/actions");
    fireEvent.click(
      screen.getByRole("button", { name: "업무가 한 사람에게 몰려 있어?" }),
    );

    expect(
      await screen.findByText(/1분쯤 뒤에 다시 물어봐 주세요/),
    ).toBeTruthy();
  });
});

describe("Assistant waiting proposals", () => {
  // The launcher's red dot said something waited without saying what; the
  // panel now says it first, with the way to the queue (2026-10-13).
  const WAITING = {
    id: "pend_1",
    team_id: "team_1",
    meeting_id: null,
    subagent: "workload",
    kind: "reassign",
    tool: "extraction.reassign_action_item",
    status: "pending" as PendingStatus,
    reject_reason: null,
    result_ok: null,
    created_at: "2026-10-03T00:00:00Z",
    decided_at: null,
    title: "재배정",
    body: "",
    needs_check: false,
  };

  it("says how many wait and links to the queue when the panel opens", async () => {
    vi.spyOn(api, "listPending").mockResolvedValue([
      WAITING,
      { ...WAITING, id: "pend_2" },
    ]);

    open("/");

    expect(
      await screen.findByText(/승인을 기다리는 제안이 2건 있습니다/),
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "승인 대기 열기" }).getAttribute("href"),
    ).toBe("/approvals");
  });

  it("puts no dot on the launcher", async () => {
    const listed = vi.spyOn(api, "listPending").mockResolvedValue([WAITING]);
    render(
      <Assistant teamId="team_1" teamName="A팀" userName="민경" pathname="/" />,
    );
    await waitFor(() => expect(listed).toHaveBeenCalled());
    expect(
      screen.queryByLabelText("승인을 기다리는 제안이 있습니다"),
    ).toBeNull();
  });

  async function shownFor(
    answer: () => Promise<PendingAction[]>,
  ): Promise<boolean> {
    const listed = vi.spyOn(api, "listPending").mockImplementation(answer);
    open("/");
    await waitFor(() => expect(listed).toHaveBeenCalled());
    return screen.queryByText(/승인을 기다리는 제안이/) !== null;
  }

  it("says nothing when nothing waits", async () => {
    expect(await shownFor(async () => [])).toBe(false);
  });

  it("says nothing of an approval interrupted mid-run", async () => {
    // It comes back as needs_check and is never re-run: nothing to approve (#759 review).
    const interrupted = {
      ...WAITING,
      status: "approved" as PendingStatus,
      needs_check: true,
    };
    expect(await shownFor(async () => [interrupted])).toBe(false);
  });

  it("says nothing when the queue cannot be read", async () => {
    expect(
      await shownFor(async () => {
        throw new Error("offline");
      }),
    ).toBe(false);
  });
});

describe("Assistant launcher shortcut", () => {
  it("shows both the Mac and the Windows shortcut", () => {
    vi.spyOn(api, "listPending").mockResolvedValue([]);
    render(
      <Assistant teamId="team_1" teamName="A팀" userName="민경" pathname="/" />,
    );

    const launcher = screen.getByRole("button", {
      name: "Autune 비서 열기 (Ctrl+J / ⌘J)",
    });
    // Ctrl J first, then ⌘J.
    expect(launcher.textContent).toMatch(/Ctrl J.*⌘J/);
  });

  it("opens on Ctrl+J as well as ⌘J", () => {
    vi.spyOn(api, "listPending").mockResolvedValue([]);
    render(
      <Assistant teamId="team_1" teamName="A팀" userName="민경" pathname="/" />,
    );
    const launcher = screen.getByRole("button", { name: /Autune 비서 열기/ });

    fireEvent.keyDown(window, { key: "j", ctrlKey: true });
    expect(launcher.getAttribute("aria-expanded")).toBe("true");

    fireEvent.keyDown(window, { key: "j", metaKey: true });
    expect(launcher.getAttribute("aria-expanded")).toBe("false");
  });
});

describe("Assistant reply", () => {
  // #862: the subagent answers before L1 runs, so what did not go through
  // comes with the reply, each with why.
  it("says what did not go through and why", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(api, "sendChat").mockResolvedValue({
      run_id: "run_1",
      outcome: "answered",
      route: "report",
      answer: "다시 만들어 볼게요.",
      items: [],
      proposed: 1,
      executed: 0,
      queued: 0,
      pending: [],
      unfinished: [
        { title: "리포트 초안 다시 만들기", reason: "초안이 바뀌었습니다" },
      ],
    });

    open("/actions");
    fireEvent.click(
      screen.getByRole("button", { name: "업무가 한 사람에게 몰려 있어?" }),
    );

    expect(await screen.findByText("처리하지 못한 것 1건")).toBeTruthy();
    expect(
      screen.getByText("리포트 초안 다시 만들기 — 초안이 바뀌었습니다"),
    ).toBeTruthy();
    expect(screen.queryByText(/바로 처리한 것/)).toBeNull();
  });
});
