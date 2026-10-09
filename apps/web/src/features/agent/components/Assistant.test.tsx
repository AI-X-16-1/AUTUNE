import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { PendingAction } from "../types";

type PendingStatus = PendingAction["status"];
import { Assistant } from "./Assistant";

// S34 header: "{meeting title} 보고 있음" on a meeting page, the team and the
// page's name elsewhere (docs/design/agent-assistant.md 3.1, #1055).

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function open(pathname: string): void {
  render(<Assistant teamId="team_1" teamName="A팀" userName="민경" pathname={pathname} />);
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

    expect(screen.getByText("A팀 · 액션아이템 보고 있음")).toBeTruthy();
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

describe("Assistant launcher alert", () => {
  // agent-assistant.md section 2: a 6px signal.critical dot when the assistant
  // has something to say first; the nearest signal is the approvals queue (9.7).
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

  it("shows a dot while a proposal waits for this person", async () => {
    vi.spyOn(api, "listPending").mockResolvedValue([WAITING]);

    render(<Assistant teamId="team_1" teamName="A팀" userName="민경" pathname="/" />);

    expect(
      await screen.findByLabelText("승인을 기다리는 제안이 있습니다"),
    ).toBeTruthy();
  });

  async function shownFor(
    answer: () => Promise<PendingAction[]>,
  ): Promise<boolean> {
    const listed = vi.spyOn(api, "listPending").mockImplementation(answer);
    render(<Assistant teamId="team_1" teamName="A팀" userName="민경" pathname="/" />);
    await waitFor(() => expect(listed).toHaveBeenCalled());
    return screen.queryByLabelText("승인을 기다리는 제안이 있습니다") !== null;
  }

  it("shows nothing when nothing waits", async () => {
    expect(await shownFor(async () => [])).toBe(false);
  });

  it("shows nothing for an approval interrupted mid-run", async () => {
    // It comes back as needs_check and is never re-run: nothing to approve (#759 review).
    const interrupted = {
      ...WAITING,
      status: "approved" as PendingStatus,
      needs_check: true,
    };
    expect(await shownFor(async () => [interrupted])).toBe(false);
  });

  it("shows nothing when the queue cannot be read", async () => {
    expect(
      await shownFor(async () => {
        throw new Error("offline");
      }),
    ).toBe(false);
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
