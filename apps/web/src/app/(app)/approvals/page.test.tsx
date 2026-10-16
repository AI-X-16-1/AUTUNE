import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { rememberTeam } from "@/features/transcript";

import ApprovalsPage from "./page";

// The route hands 승인 대기 the team chosen in the sidebar, or the person's
// first team, from the session -- never a team it waits on for good (#1322
// review). Only the joining is tested here; the screen has its own tests.

vi.mock("@/features/agent", () => ({
  ApprovalsScreen: ({ teamId }: { teamId: string | null | undefined }) => (
    <output>{teamId === undefined ? "unknown" : (teamId ?? "no team")}</output>
  ),
}));
const user = vi.fn();
vi.mock("../../_components/SessionGate", () => ({ useSessionUser: () => user() }));

const ME = (teams: { id: string; name: string }[]) => ({
  id: "user_me",
  email: "me@example.com",
  display_name: "Me",
  teams,
});
const handed = () => screen.getByRole("status").textContent;

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  user.mockReset();
});

describe("the 승인 대기 route and the sidebar's team", () => {
  it("hands over the remembered team", () => {
    window.localStorage.setItem("autune.team", "team_b");
    user.mockReturnValue(ME([{ id: "team_a", name: "A" }, { id: "team_b", name: "B" }]));

    render(<ApprovalsPage />);

    expect(handed()).toBe("team_b");
  });

  it("hands over the first team when none is remembered", () => {
    user.mockReturnValue(ME([{ id: "team_a", name: "A" }, { id: "team_b", name: "B" }]));

    render(<ApprovalsPage />);

    expect(handed()).toBe("team_a");
  });

  it("hands over the first team when the remembered one was left", () => {
    window.localStorage.setItem("autune.team", "team_gone");
    user.mockReturnValue(ME([{ id: "team_a", name: "A" }]));

    render(<ApprovalsPage />);

    expect(handed()).toBe("team_a");
  });

  it("says there is no team for somebody on none", () => {
    user.mockReturnValue(ME([]));

    render(<ApprovalsPage />);

    expect(handed()).toBe("no team");
  });

  it("follows a team chosen while it is open", () => {
    user.mockReturnValue(ME([{ id: "team_a", name: "A" }, { id: "team_b", name: "B" }]));
    render(<ApprovalsPage />);

    act(() => rememberTeam("team_b"));

    expect(handed()).toBe("team_b");
  });
});
