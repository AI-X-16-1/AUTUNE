import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { Assistant } from "./Assistant";

// S34 header: "{meeting title} 보고 있음" on a meeting page, the page's name
// elsewhere (docs/design/agent-assistant.md 3.1).

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function open(pathname: string): void {
  render(<Assistant teamId="team_1" userName="민경" pathname={pathname} />);
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

    expect(screen.getByText("액션아이템 보고 있음")).toBeTruthy();
    expect(label).not.toHaveBeenCalled();
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
    status: "pending" as const,
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

    render(<Assistant teamId="team_1" userName="민경" pathname="/" />);

    expect(
      await screen.findByLabelText("승인을 기다리는 제안이 있습니다"),
    ).toBeTruthy();
  });

  it("shows nothing when nothing waits, or the queue cannot be read", async () => {
    const listed = vi.spyOn(api, "listPending").mockResolvedValueOnce([]);
    render(<Assistant teamId="team_1" userName="민경" pathname="/" />);
    await waitFor(() => expect(listed).toHaveBeenCalled());
    expect(
      screen.queryByLabelText("승인을 기다리는 제안이 있습니다"),
    ).toBeNull();
    cleanup();

    listed.mockRejectedValueOnce(new Error("offline"));
    render(<Assistant teamId="team_1" userName="민경" pathname="/" />);
    await waitFor(() => expect(listed).toHaveBeenCalledTimes(2));
    expect(
      screen.queryByLabelText("승인을 기다리는 제안이 있습니다"),
    ).toBeNull();
  });
});
