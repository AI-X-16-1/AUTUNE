import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TeamMenu } from "./TeamMenu";
import { TeamScope } from "./TeamScope";
import type { TeamSummary } from "../types";

// The team is chosen in the sidebar (the user, 2026-10-06): the menu and a
// screen's row are two views of one choice, and each follows the other.

const pathname = vi.fn(() => "/");
const push = vi.fn<(to: string) => void>();
vi.mock("next/navigation", () => ({
  usePathname: () => pathname(),
  useRouter: () => ({ push }),
}));

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
// The teams listed in the sidebar itself: not the control that opens the
// rest, and not what the small window lists.
const names = () =>
  [...menu().querySelectorAll("button[aria-pressed]")]
    .filter((b) => !b.closest('[role="dialog"]'))
    .map((b) => b.textContent);
const rest = () => screen.queryByRole("dialog", { name: "다른 팀" });
const inRest = () =>
  [...(rest()?.querySelectorAll("button") ?? [])].map((b) => b.textContent);
const more = () => menu().querySelector("button[aria-expanded]") as HTMLButtonElement | null;

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
  push.mockReset();
  pathname.mockImplementation(() => "/");
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

  it("says how many more there are, and shows them in a small window when pressed", async () => {
    // The user, 2026-10-06: "누르면 남은 팀들 보이게", and "사이드바에 직접
    // 늘리지 말고 작은 화면 띄워서 보여줘".
    open([A, B, C, D, E]);
    await waitFor(() => expect(names()).toEqual(["가 팀", "나 팀", "다 팀"]));

    expect(more()?.textContent).toBe("다른 팀 2개");
    expect(more()?.getAttribute("aria-expanded")).toBe("false");
    expect(rest()).toBeNull();
    fireEvent.click(more() as HTMLButtonElement);

    // The rest, and only the rest, in a window of its own ...
    expect(inRest()).toEqual(["라 팀", "마 팀"]);
    expect(more()?.getAttribute("aria-expanded")).toBe("true");
    // ... placed over the page, so that the sidebar's own list is still three.
    expect((rest() as HTMLElement).style.position).toBe("fixed");
    expect(names()).toEqual(["가 팀", "나 팀", "다 팀"]);
    // Pressed again, it is gone.
    fireEvent.click(more() as HTMLButtonElement);
    expect(rest()).toBeNull();
  });

  it("a team picked there is the team on show, among the three, and the window closes", async () => {
    open([A, B, C, D, E], true);
    await waitFor(() => expect(shown()).toBe("team_a"));
    fireEvent.click(more() as HTMLButtonElement);

    fireEvent.click(entry("마 팀"));

    expect(shown()).toBe("team_e");
    expect(names()).toEqual(["가 팀", "나 팀", "마 팀"]);
    expect(chosen()).toEqual(["마 팀"]);
    expect(rest()).toBeNull();
    expect(more()?.textContent).toBe("다른 팀 2개");
    expect(window.localStorage.getItem("autune.team")).toBe("team_e");
  });

  it.each(["/meetings/mtg_1", "/meetings/mtg_1/actions", "/meetings/mtg_1/summary"])(
    "on %s another team pressed goes to that team's meetings",
    async (path) => {
      // The user, 2026-10-06: "회의 상태에서 사이드바에 다른 팀 누르면 해당 팀의
      // 회의로 이동". Home lists the chosen team's meetings.
      pathname.mockImplementation(() => path);
      open([A, B, C]);
      await waitFor(() => expect(chosen()).toEqual(["가 팀"]));

      fireEvent.click(entry("나 팀"));

      expect(window.localStorage.getItem("autune.team")).toBe("team_b");
      expect(push).toHaveBeenCalledTimes(1);
      expect(push).toHaveBeenCalledWith("/");
    },
  );

  it("the team already marked goes nowhere, inside a meeting too", async () => {
    pathname.mockImplementation(() => "/meetings/mtg_1");
    open([A, B, C]);
    await waitFor(() => expect(chosen()).toEqual(["가 팀"]));

    fireEvent.click(entry("가 팀"));

    expect(push).not.toHaveBeenCalled();
  });

  it("a team picked from the small window inside a meeting goes there as well", async () => {
    pathname.mockImplementation(() => "/meetings/mtg_1/gap");
    open([A, B, C, D, E]);
    await waitFor(() => expect(chosen()).toEqual(["가 팀"]));
    fireEvent.click(more() as HTMLButtonElement);

    fireEvent.click(entry("마 팀"));

    expect(push).toHaveBeenCalledWith("/");
    expect(window.localStorage.getItem("autune.team")).toBe("team_e");
  });

  it.each(["/meetings/new", "/meetings/new?meeting=mtg_1", "/", "/actions", "/dashboard"])(
    "on %s the choice changes and the page stays",
    async (path) => {
      // "회의 시작" may hold a recording or an upload in progress: a press in
      // the sidebar must not drop it. Elsewhere the screens follow by themselves.
      pathname.mockImplementation(() => path.split("?")[0] as string);
      open([A, B, C]);
      await waitFor(() => expect(chosen()).toEqual(["가 팀"]));

      fireEvent.click(entry("나 팀"));

      expect(chosen()).toEqual(["나 팀"]);
      expect(push).not.toHaveBeenCalled();
    },
  );

  it("opens just past the sidebar's edge, level with the control", async () => {
    // Seen in a browser: placed from the control alone it lay half on the
    // sidebar, because the control ends short of the sidebar's edge.
    const rect = (right: number, top: number) => ({ right, top }) as DOMRect;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      if (this.tagName === "ASIDE") return rect(200, 0);
      if (this.hasAttribute("aria-expanded")) return rect(181, 249);
      return rect(0, 0);
    });
    list.mockResolvedValue([A, B, C, D, E]);
    render(
      <aside>
        <TeamMenu />
      </aside>,
    );
    await waitFor(() => expect(names()).toEqual(["가 팀", "나 팀", "다 팀"]));

    fireEvent.click(more() as HTMLButtonElement);

    const style = (rest() as HTMLElement).style;
    expect([style.left, style.top]).toEqual(["208px", "249px"]);
    vi.restoreAllMocks();
  });

  it("stays on the page when the control is near the bottom of it", async () => {
    const rect = (right: number, top: number) => ({ right, top }) as DOMRect;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.hasAttribute("aria-expanded") ? rect(181, window.innerHeight - 20) : rect(0, 0);
    });
    open([A, B, C, D, E]);
    await waitFor(() => expect(names()).toEqual(["가 팀", "나 팀", "다 팀"]));

    fireEvent.click(more() as HTMLButtonElement);

    // Two teams: about 86px of window, and 8px kept clear under it.
    const top = Number.parseInt((rest() as HTMLElement).style.top, 10);
    expect(top).toBe(window.innerHeight - 86 - 8);
    vi.restoreAllMocks();
  });

  it("closes on Escape and on a press outside it, and not on a press inside", async () => {
    open([A, B, C, D, E], true);
    await waitFor(() => expect(shown()).toBe("team_a"));

    fireEvent.click(more() as HTMLButtonElement);
    fireEvent.mouseDown(rest() as HTMLElement);
    expect(rest()).not.toBeNull();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(rest()).toBeNull();

    fireEvent.click(more() as HTMLButtonElement);
    fireEvent.mouseDown(screen.getByTestId("shown"));
    expect(rest()).toBeNull();
    // Nothing was chosen by closing it.
    expect(shown()).toBe("team_a");
  });

  it("has no such control when there is nothing more to show", async () => {
    open([A, B, C]);

    await waitFor(() => expect(names()).toEqual(["가 팀", "나 팀", "다 팀"]));
    expect(more()).toBeNull();
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
