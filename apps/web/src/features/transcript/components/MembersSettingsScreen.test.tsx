import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MembersSettingsScreen } from "./MembersSettingsScreen";

// A team is what a project gets today: its own members and meetings. Someone
// already on one reaches S02 again from here.

vi.mock("../api", () => ({
  listTeams: () => Promise.resolve([{ team_id: "team_1", name: "Alpha" }]),
  listTeamMembers: () => Promise.resolve([]),
  inviteToTeam: vi.fn(),
}));
vi.mock("@/shared/api/auth", () => ({
  getGmailConnection: () => Promise.resolve(null),
  googleGmailConnectUrl: () => "",
  disconnectGmail: vi.fn(),
}));

afterEach(cleanup);

describe("MembersSettingsScreen", () => {
  it("links to making another team for a project with other members", () => {
    render(<MembersSettingsScreen />);

    const link = screen.getByRole("link", { name: "새 팀 만들기" });
    expect(link.getAttribute("href")).toBe("/workspace/new");
  });
});
