import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { TeamScope } from "./TeamScope";
import type { TeamSummary } from "../types";

// Pinning a team from the row that chooses one (the user, 2026-10-02): the
// list comes pinned first, the first is the default, and a pin changes the
// order -- not the team the screen is showing.

const list = vi.fn<() => Promise<TeamSummary[]>>();
const pin = vi.fn<(teamId: string) => Promise<TeamSummary[]>>();
const unpin = vi.fn<(teamId: string) => Promise<TeamSummary[]>>();
vi.mock("../api", () => ({
  listTeams: () => list(),
  pinTeam: (teamId: string) => pin(teamId),
  unpinTeam: (teamId: string) => unpin(teamId),
}));

const A: TeamSummary = { team_id: "team_a", name: "가 팀", pinned: false };
const B: TeamSummary = { team_id: "team_b", name: "나 팀", pinned: false };

const shown = () => screen.getByTestId("shown").textContent;
const chips = () => screen.getAllByRole("button", { pressed: undefined }).map((b) => b.textContent);

function open(teams: TeamSummary[]) {
  list.mockResolvedValue(teams);
  render(<TeamScope>{(teamId) => <div data-testid="shown">{teamId}</div>}</TeamScope>);
}

afterEach(() => {
  cleanup();
  list.mockReset();
  pin.mockReset();
  unpin.mockReset();
});

describe("TeamScope", () => {
  it("opens on the first team of the list, which is the pinned one", async () => {
    open([{ ...B, pinned: true }, A]);

    await waitFor(() => expect(shown()).toBe("team_b"));
    expect(screen.getByRole("button", { name: "나 팀 · 고정" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "고정 해제" })).toBeTruthy();
  });

  it("pins the team on screen, reorders the row and keeps showing that team", async () => {
    open([A, B]);
    await waitFor(() => expect(shown()).toBe("team_a"));
    fireEvent.click(screen.getByRole("button", { name: "나 팀" }));
    expect(shown()).toBe("team_b");
    pin.mockResolvedValue([{ ...B, pinned: true }, A]);

    fireEvent.click(screen.getByRole("button", { name: "맨 위에 고정" }));

    await screen.findByRole("button", { name: "나 팀 · 고정" });
    expect(pin).toHaveBeenCalledExactlyOnceWith("team_b");
    expect(chips().slice(0, 2)).toEqual(["나 팀 · 고정", "가 팀"]);
    expect(shown()).toBe("team_b");
  });

  it("takes the pin off", async () => {
    open([{ ...B, pinned: true }, A]);
    await waitFor(() => expect(shown()).toBe("team_b"));
    unpin.mockResolvedValue([A, B]);

    fireEvent.click(screen.getByRole("button", { name: "고정 해제" }));

    await screen.findByRole("button", { name: "맨 위에 고정" });
    expect(unpin).toHaveBeenCalledExactlyOnceWith("team_b");
    expect(chips().slice(0, 2)).toEqual(["가 팀", "나 팀"]);
    expect(shown()).toBe("team_b");
  });

  it("says three is the limit when a fourth pin is refused", async () => {
    open([A, B]);
    await waitFor(() => expect(shown()).toBe("team_a"));
    pin.mockRejectedValue(
      new ApiError(409, "too_many_pinned_teams", "at most 3 teams can be pinned", { limit: 3 }),
    );

    fireEvent.click(screen.getByRole("button", { name: "맨 위에 고정" }));

    expect((await screen.findByRole("alert")).textContent).toContain("3개까지 고정할 수 있습니다");
    expect(chips().slice(0, 2)).toEqual(["가 팀", "나 팀"]);
  });

  it("says so in its own words when the pin could not be changed", async () => {
    open([A, B]);
    await waitFor(() => expect(shown()).toBe("team_a"));
    pin.mockRejectedValue(new Error("you are not a member of this team"));

    fireEvent.click(screen.getByRole("button", { name: "맨 위에 고정" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "고정을 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.",
    );
  });

  it("offers no pin to somebody on one team", async () => {
    open([A]);

    await waitFor(() => expect(shown()).toBe("team_a"));
    expect(screen.queryByRole("button", { name: "맨 위에 고정" })).toBeNull();
  });
});
