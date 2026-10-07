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
const C: TeamSummary = { team_id: "team_c", name: "다 팀", pinned: false };
const D: TeamSummary = { team_id: "team_d", name: "라 팀", pinned: false };
const E: TeamSummary = { team_id: "team_e", name: "마 팀", pinned: false };

const more = () => screen.queryByRole("button", { name: "팀 더보기" });
const win = () => screen.queryByRole("dialog", { name: "팀" });
// The teams the row itself lists: its chips, not what the window lists.
const rowTeams = () =>
  screen
    .getAllByRole("button")
    .filter((b) => b.hasAttribute("aria-pressed") && !b.closest('[role="dialog"]'))
    .map((b) => b.textContent);
const windowTeams = () =>
  [...(win()?.querySelectorAll("button[aria-pressed]") ?? [])].map((b) => b.textContent);

const shown = () => screen.getByTestId("shown").textContent;
const chips = () => screen.getAllByRole("button", { pressed: undefined }).map((b) => b.textContent);

function open(teams: TeamSummary[]) {
  list.mockResolvedValue(teams);
  render(<TeamScope>{(teamId) => <div data-testid="shown">{teamId}</div>}</TeamScope>);
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
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

  // The user, 2026-10-06: a team once chosen stays chosen on the next menu,
  // until the person picks another. Each screen mounts its own row, so the
  // second `open` below is "another menu".
  it("opens on the team the person chose on another screen, not on the first", async () => {
    open([{ ...A, pinned: true }, B]);
    await waitFor(() => expect(shown()).toBe("team_a"));
    fireEvent.click(screen.getByRole("button", { name: "나 팀" }));
    cleanup();

    open([{ ...A, pinned: true }, B]);

    await waitFor(() => expect(shown()).toBe("team_b"));
  });

  it("follows the next choice, and only a choice", async () => {
    open([A, B]);
    await waitFor(() => expect(shown()).toBe("team_a"));
    // Opening on the default is not a choice: a pin can still move the default.
    expect(window.localStorage.getItem("autune.team")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "나 팀" }));
    fireEvent.click(screen.getByRole("button", { name: "가 팀" }));
    cleanup();

    open([B, A]);

    await waitFor(() => expect(shown()).toBe("team_a"));
  });

  it("opens on the first team when the chosen one is a team they are no longer on", async () => {
    window.localStorage.setItem("autune.team", "team_left");

    open([A, B]);

    await waitFor(() => expect(shown()).toBe("team_a"));
  });

  it("forgets, and still works, in a browser that refuses storage", async () => {
    const refuse = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    const refuseWrite = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });

    open([A, B]);
    await waitFor(() => expect(shown()).toBe("team_a"));
    fireEvent.click(screen.getByRole("button", { name: "나 팀" }));

    expect(shown()).toBe("team_b");
    refuse.mockRestore();
    refuseWrite.mockRestore();
  });

  // The user, 2026-10-07: "내부의 팀 목록들도 사이드바처럼 3개만 보이고 더보기로".
  it("lists three teams and 더보기 to somebody on more than three", async () => {
    open([A, B, C, D, E]);
    await waitFor(() => expect(shown()).toBe("team_a"));

    expect(rowTeams()).toEqual(["가 팀", "나 팀", "다 팀"]);
    expect(more()).not.toBeNull();
    expect(win()).toBeNull();
  });

  it("shows every team and no 더보기 to somebody on three or fewer", async () => {
    open([A, B, C]);
    await waitFor(() => expect(shown()).toBe("team_a"));

    expect(rowTeams()).toEqual(["가 팀", "나 팀", "다 팀"]);
    expect(more()).toBeNull();
  });

  it("더보기 opens every team, and one picked there is the team on screen, in the row", async () => {
    open([A, B, C, D, E]);
    await waitFor(() => expect(shown()).toBe("team_a"));

    fireEvent.click(more() as HTMLElement);
    expect(windowTeams()).toEqual(["가 팀", "나 팀", "다 팀", "라 팀", "마 팀"]);
    expect(rowTeams()).toEqual(["가 팀", "나 팀", "다 팀"]);

    fireEvent.click(
      [...(win() as HTMLElement).querySelectorAll("button[aria-pressed]")].find(
        (b) => b.textContent === "마 팀",
      ) as HTMLElement,
    );

    expect(shown()).toBe("team_e");
    expect(win()).toBeNull();
    // Still three, the team on screen among them, in the last place.
    expect(rowTeams()).toEqual(["가 팀", "나 팀", "마 팀"]);
  });

  it("opens with the team chosen elsewhere among the three", async () => {
    window.localStorage.setItem("autune.team", "team_d");
    open([A, B, C, D, E]);

    await waitFor(() => expect(shown()).toBe("team_d"));
    expect(rowTeams()).toEqual(["가 팀", "나 팀", "라 팀"]);
  });

  it("the window closes on Escape and on a press outside it", async () => {
    open([A, B, C, D]);
    await waitFor(() => expect(shown()).toBe("team_a"));

    fireEvent.click(more() as HTMLElement);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(win()).toBeNull();

    fireEvent.click(more() as HTMLElement);
    fireEvent.mouseDown(screen.getByTestId("shown"));
    expect(win()).toBeNull();
    expect(shown()).toBe("team_a");
  });

  it("keeps the pin button in the row beside 더보기", async () => {
    open([A, B, C, D]);
    await waitFor(() => expect(shown()).toBe("team_a"));

    expect(screen.getByRole("button", { name: "맨 위에 고정" })).toBeTruthy();
  });

  it("offers no pin to somebody on one team", async () => {
    open([A]);

    await waitFor(() => expect(shown()).toBe("team_a"));
    expect(screen.queryByRole("button", { name: "맨 위에 고정" })).toBeNull();
  });
});
