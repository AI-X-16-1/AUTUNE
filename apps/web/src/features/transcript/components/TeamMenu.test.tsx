import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TeamMenu } from "./TeamMenu";
import { TeamScope } from "./TeamScope";
import type { TeamSummary } from "../types";

// The team is chosen in the sidebar (the user, 2026-10-06): the menu and a
// screen's row are two views of one choice, and each follows the other.

const list = vi.fn<() => Promise<TeamSummary[]>>();
vi.mock("../api", () => ({
  listTeams: () => list(),
  pinTeam: vi.fn(),
  unpinTeam: vi.fn(),
}));

const A: TeamSummary = { team_id: "team_a", name: "가 팀", pinned: false };
const B: TeamSummary = { team_id: "team_b", name: "나 팀", pinned: false };
const C: TeamSummary = { team_id: "team_c", name: "다 팀", pinned: false };
const D: TeamSummary = { team_id: "team_d", name: "라 팀", pinned: false };
const E: TeamSummary = { team_id: "team_e", name: "마 팀", pinned: false };
const names = () => [...menu().querySelectorAll("button")].map((b) => b.textContent);

const menu = () => screen.getByRole("navigation", { name: "팀" });
const entry = (name: string) =>
  [...menu().querySelectorAll("button")].find((b) => b.textContent === name) as HTMLButtonElement;
const chosen = () =>
  [...menu().querySelectorAll('button[aria-pressed="true"]')].map((b) => b.textContent);
const shown = () => screen.getByTestId("shown").textContent;

function open(teams: TeamSummary[], withScreen = false) {
  list.mockResolvedValue(teams);
  render(
    <>
      <TeamMenu />
      {withScreen ? (
        <TeamScope>{(teamId) => <div data-testid="shown">{teamId}</div>}</TeamScope>
      ) : null}
    </>,
  );
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  list.mockReset();
});

describe("TeamMenu", () => {
  it("lists the person's teams with the one they are looking at marked", async () => {
    window.localStorage.setItem("autune.team", "team_b");

    open([A, B]);

    await waitFor(() => expect(chosen()).toEqual(["나 팀"]));
    expect([...menu().querySelectorAll("button")].map((b) => b.textContent)).toEqual([
      "가 팀",
      "나 팀",
    ]);
  });

  it("marks the first team for somebody who has not chosen one", async () => {
    open([A, B]);

    await waitFor(() => expect(chosen()).toEqual(["가 팀"]));
    expect(window.localStorage.getItem("autune.team")).toBeNull();
  });

  it("a team picked here is the team of the screen on show, at once, and is kept", async () => {
    open([A, B], true);
    await waitFor(() => expect(shown()).toBe("team_a"));

    fireEvent.click(entry("나 팀"));

    expect(shown()).toBe("team_b");
    expect(chosen()).toEqual(["나 팀"]);
    expect(window.localStorage.getItem("autune.team")).toBe("team_b");
  });

  it("lists three teams and no more, the first three as the server orders them", async () => {
    // The user, 2026-10-06. `GET /teams` puts pinned teams first, so a pinned
    // team is among the three before any that is not.
    open([{ ...D, pinned: true }, { ...B, pinned: true }, A, C, E]);

    await waitFor(() => expect(names()).toEqual(["라 팀", "나 팀", "가 팀"]));
    expect(chosen()).toEqual(["라 팀"]);
  });

  it("keeps the team being looked at among the three, in the last place", async () => {
    window.localStorage.setItem("autune.team", "team_e");

    open([A, B, C, D, E]);

    await waitFor(() => expect(names()).toEqual(["가 팀", "나 팀", "마 팀"]));
    expect(chosen()).toEqual(["마 팀"]);
  });

  it("brings in a team chosen in a screen's row, and stays at three", async () => {
    open([A, B, C, D, E], true);
    await waitFor(() => expect(shown()).toBe("team_a"));
    const row = screen
      .getAllByRole("button", { name: "라 팀" })
      .find((b) => !menu().contains(b)) as HTMLElement;

    fireEvent.click(row);

    expect(names()).toEqual(["가 팀", "나 팀", "라 팀"]);
    expect(chosen()).toEqual(["라 팀"]);
    // Back to one of the first three: the first three again.
    fireEvent.click(entry("나 팀"));
    expect(names()).toEqual(["가 팀", "나 팀", "다 팀"]);
  });

  it("the screen's own row still offers every team", async () => {
    open([A, B, C, D, E], true);
    await waitFor(() => expect(shown()).toBe("team_a"));

    for (const team of [A, B, C, D, E]) {
      const outside = screen
        .getAllByRole("button", { name: team.name })
        .filter((b) => !menu().contains(b));
      expect(outside).toHaveLength(1);
    }
  });

  it("follows a team picked in a screen's row", async () => {
    open([A, B], true);
    await waitFor(() => expect(shown()).toBe("team_a"));
    const row = screen
      .getAllByRole("button", { name: "나 팀" })
      .find((b) => !menu().contains(b)) as HTMLElement;

    fireEvent.click(row);

    expect(shown()).toBe("team_b");
    expect(chosen()).toEqual(["나 팀"]);
  });

  it("still has the menu and the screen agree in a browser that refuses storage", async () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    open([A, B], true);
    await waitFor(() => expect(shown()).toBe("team_a"));

    fireEvent.click(entry("나 팀"));

    expect(shown()).toBe("team_b");
    expect(chosen()).toEqual(["나 팀"]);
    get.mockRestore();
    set.mockRestore();
  });

  it("shows one team's name with nothing to choose", async () => {
    open([A]);

    expect(await screen.findByText("가 팀")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows nothing to somebody on no team, or when the list cannot be read", async () => {
    list.mockResolvedValue([]);
    const none = render(<TeamMenu />);
    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(none.container.textContent).toBe("");
    cleanup();

    list.mockRejectedValue(new Error("offline"));
    const failed = render(<TeamMenu />);
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(failed.container.textContent).toBe("");
  });
});
