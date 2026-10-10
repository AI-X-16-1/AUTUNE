import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SlackConnect } from "./SlackConnect";

// The team's Slack and the person's own Slack link (DM 받기) in one place, the
// second only once the first is connected (the user, 2026-10-04).

const connection = vi.fn();
vi.mock("@/shared/api/auth", () => ({
  getSlackConnection: () => connection(),
  slackConnectUrl: () => "/connect",
  disconnectSlack: vi.fn(),
  getSlackMe: () =>
    Promise.resolve({ linked: false, pending: false, workspace_name: null }),
  slackMeConnectUrl: () => "/me",
  unlinkSlackMe: vi.fn(),
}));

afterEach(() => {
  cleanup();
  connection.mockReset();
});

describe("SlackConnect", () => {
  it("offers DM 받기 under the team's Slack once it is connected", async () => {
    connection.mockResolvedValue({
      connected: true,
      workspace_name: "Acme",
      channel_name: "제품팀",
      channel_url: null,
    });

    render(<SlackConnect teamId="team_1" />);

    await screen.findByText(/Slack 연결됨/);
    expect(
      await screen.findByRole("button", {
        name: "내 Slack 계정 연결 (DM 받기)",
      }),
    ).toBeTruthy();
  });

  it("has no DM 받기 while the team's Slack is not connected", async () => {
    connection.mockResolvedValue({ connected: false });

    render(<SlackConnect teamId="team_1" />);

    await screen.findByRole("button", { name: "팀 Slack 연결" });
    expect(screen.queryByText(/DM 받기/)).toBeNull();
  });

  it("says it is not connected, beside a button that says 연결 (#1183)", async () => {
    connection.mockResolvedValue({ connected: false });

    render(<SlackConnect teamId="team_1" />);

    const button = await screen.findByRole("button", { name: "팀 Slack 연결" });
    expect(button.textContent).toBe("연결");
    expect(screen.getByText("Slack · 연결 안 됨")).toBeTruthy();
  });
});
