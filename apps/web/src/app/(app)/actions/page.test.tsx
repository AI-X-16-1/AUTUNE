import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { rememberTeam } from "@/features/transcript";

import ActionsPage from "./page";

// The route joins the board (module B's) to the sidebar's team menu (module
// A's): the two features do not import each other. What matters here is only
// the joining -- which team the board is handed, and when.

vi.mock("@/features/actions", () => ({
  TeamActionsScreen: ({
    teamId,
    onEveryTeam,
  }: {
    teamId: string | null;
    onEveryTeam: () => void;
  }) => (
    <div>
      <output>{teamId ?? "every team"}</output>
      <button type="button" onClick={onEveryTeam}>
        back
      </button>
    </div>
  ),
}));
vi.mock("../../_components/SessionGate", () => ({ useSessionUser: () => null }));

const handed = () => screen.getByRole("status").textContent;

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("the 액션 아이템 route and the sidebar's team", () => {
  it("opens on every team, whichever team was chosen on another screen", () => {
    window.localStorage.setItem("autune.team", "team_a");

    render(<ActionsPage />);

    expect(handed()).toBe("every team");
  });

  it("hands the board a team pressed while it is open, the marked one too", () => {
    window.localStorage.setItem("autune.team", "team_a");
    render(<ActionsPage />);

    act(() => rememberTeam("team_b"));
    expect(handed()).toBe("team_b");

    act(() => rememberTeam("team_a"));
    expect(handed()).toBe("team_a");
  });

  it("goes back to every team when the board asks, and the choice is kept", () => {
    render(<ActionsPage />);
    act(() => rememberTeam("team_b"));

    fireEvent.click(screen.getByRole("button", { name: "back" }));

    expect(handed()).toBe("every team");
    expect(window.localStorage.getItem("autune.team")).toBe("team_b");
  });

  it("stops listening when the screen is left", () => {
    const view = render(<ActionsPage />);
    view.unmount();

    expect(() => act(() => rememberTeam("team_b"))).not.toThrow();
  });
});
