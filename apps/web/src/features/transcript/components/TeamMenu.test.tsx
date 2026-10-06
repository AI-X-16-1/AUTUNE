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
