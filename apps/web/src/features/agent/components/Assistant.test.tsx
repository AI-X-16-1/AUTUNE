import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
