import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { rememberTeam } from "@/features/transcript";

import { AssistantMount } from "./AssistantMount";

// #1055: the assistant answers team questions about the team chosen in the
// sidebar's menu, not always the first team the person belongs to.

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("./SessionGate", () => ({
  useSessionUser: () => ({
    id: "usr_1",
    email: "a@example.com",
    display_name: "민경",
    teams: [
      { id: "team_a", name: "A팀" },
      { id: "team_b", name: "B팀" },
    ],
  }),
}));
vi.mock("@/features/agent", () => ({
  Assistant: ({ teamId, teamName }: { teamId: string; teamName: string }) => (
    <p>{`${teamId} ${teamName}`}</p>
  ),
}));

beforeEach(() => window.localStorage.clear());
afterEach(cleanup);

describe("AssistantMount", () => {
  it("opens on the team remembered from the sidebar", () => {
    window.localStorage.setItem("autune.team", "team_b");

    render(<AssistantMount />);

    expect(screen.getByText("team_b B팀")).toBeTruthy();
  });

  it("follows a team chosen while it is open", () => {
    render(<AssistantMount />);
    expect(screen.getByText("team_a A팀")).toBeTruthy();

    act(() => rememberTeam("team_b"));

    expect(screen.getByText("team_b B팀")).toBeTruthy();
  });

  it("takes the first team when the remembered one is not the person's", () => {
    window.localStorage.setItem("autune.team", "team_left");

    render(<AssistantMount />);

    expect(screen.getByText("team_a A팀")).toBeTruthy();
  });
});
