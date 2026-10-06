import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { TeamMenu } from "./TeamMenu";
import { TeamScope } from "./TeamScope";
import type { TeamSummary } from "../types";

// The team is chosen in the sidebar (the user, 2026-10-06): the menu and a
// screen's row are two views of one choice, and each follows the other. The
// sidebar lists three teams; "더보기" opens every team in a small window,
// which is also where a team is pinned.

const pathname = vi.fn(() => "/");
const push = vi.fn<(to: string) => void>();
vi.mock("next/navigation", () => ({
  usePathname: () => pathname(),
  useRouter: () => ({ push }),
}));

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

const menu = () => screen.getByRole("navigation", { name: "팀" });
const win = () => screen.queryByRole("dialog", { name: "팀" });
const outsideWindow = (b: Element) => !b.closest('[role="dialog"]');
// The teams listed in the sidebar itself: not "더보기", not what the window lists.
const sidebarTeams = () =>
  [...menu().querySelectorAll("button[aria-pressed]")].filter(outsideWindow) as HTMLButtonElement[];
const names = () => sidebarTeams().map((b) => b.textContent);
const entry = (name: string) => sidebarTeams().find((b) => b.textContent === name) as HTMLButtonElement;
const chosen = () =>
  sidebarTeams()
    .filter((b) => b.getAttribute("aria-pressed") === "true")
    .map((b) => b.textContent);
const more = () => menu().querySelector('button[aria-haspopup="dialog"]') as HTMLButtonElement | null;
const inWindow = () =>
  [...(win()?.querySelectorAll("button[aria-pressed]") ?? [])].map((b) => b.textContent);
const windowEntry = (name: string) =>
  [...(win()?.querySelectorAll("button[aria-pressed]") ?? [])].find(
    (b) => b.textContent === name,
  ) as HTMLButtonElement;
const pinButton = (name: string) =>
  [...(win()?.querySelectorAll("button[aria-label]") ?? [])].find((b) =>
    (b.getAttribute("aria-label") ?? "").startsWith(`${name} `),
  ) as HTMLButtonElement;
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
  for (const mock of [list, pin, unpin, push]) mock.mockReset();
  pathname.mockImplementation(() => "/");
  vi.restoreAllMocks();
});

describe("TeamMenu", () => {
  it("lists the person's teams with the one they are looking at marked", async () => {
    window.localStorage.setItem("autune.team", "team_b");

    open([A, B]);

    await waitFor(() => expect(chosen()).toEqual(["나 팀"]));
    expect(names()).toEqual(["가 팀", "나 팀"]);
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
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    open([A, B], true);
    await waitFor(() => expect(shown()).toBe("team_a"));

    fireEvent.click(entry("나 팀"));

    expect(shown()).toBe("team_b");
    expect(chosen()).toEqual(["나 팀"]);
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

describe("TeamMenu, three teams", () => {
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
});

describe("TeamMenu, 더보기", () => {
  it("opens every team in a small window over the page; the sidebar stays at three", async () => {
    // The user, 2026-10-06: "누르면 남은 팀들 보이게", "사이드바에 직접 늘리지
    // 말고 작은 화면 띄워서 보여줘".
    open([A, B, C, D, E]);
    await waitFor(() => expect(names()).toEqual(["가 팀", "나 팀", "다 팀"]));

    expect(more()?.textContent).toBe("더보기");
    expect(more()?.getAttribute("aria-expanded")).toBe("false");
    expect(win()).toBeNull();
    fireEvent.click(more() as HTMLButtonElement);

    expect(inWindow()).toEqual(["가 팀", "나 팀", "다 팀", "라 팀", "마 팀"]);
    expect(more()?.getAttribute("aria-expanded")).toBe("true");
    expect((win() as HTMLElement).style.position).toBe("fixed");
    expect(names()).toEqual(["가 팀", "나 팀", "다 팀"]);
    // The team being looked at is marked there too.
    expect(windowEntry("가 팀").getAttribute("aria-pressed")).toBe("true");
    // Pressed again, it is gone.
    fireEvent.click(more() as HTMLButtonElement);
    expect(win()).toBeNull();
  });

  it("a team picked there is the team on show, among the three, and the window closes", async () => {
    open([A, B, C, D, E], true);
    await waitFor(() => expect(shown()).toBe("team_a"));
    fireEvent.click(more() as HTMLButtonElement);

    fireEvent.click(windowEntry("마 팀"));

    expect(shown()).toBe("team_e");
    expect(names()).toEqual(["가 팀", "나 팀", "마 팀"]);
    expect(chosen()).toEqual(["마 팀"]);
    expect(win()).toBeNull();
    expect(window.localStorage.getItem("autune.team")).toBe("team_e");
  });

  it("is offered to anybody on more than one team, since pinning is in it", async () => {
    open([A, B]);

    await waitFor(() => expect(names()).toEqual(["가 팀", "나 팀"]));
    expect(more()).not.toBeNull();
  });

  it("closes on Escape and on a press outside it, and not on a press inside", async () => {
    open([A, B, C, D, E], true);
    await waitFor(() => expect(shown()).toBe("team_a"));

    fireEvent.click(more() as HTMLButtonElement);
    fireEvent.mouseDown(win() as HTMLElement);
    expect(win()).not.toBeNull();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(win()).toBeNull();

    fireEvent.click(more() as HTMLButtonElement);
    fireEvent.mouseDown(screen.getByTestId("shown"));
    expect(win()).toBeNull();
    // Nothing was chosen by closing it.
    expect(shown()).toBe("team_a");
  });

  it("opens just past the sidebar's edge, level with the control", async () => {
    // Seen in a browser: placed from the control alone it lay half on the
    // sidebar, because the control ends short of the sidebar's edge.
    const rect = (right: number, top: number) => ({ right, top }) as DOMRect;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      if (this.tagName === "ASIDE") return rect(200, 0);
      if (this.hasAttribute("aria-haspopup")) return rect(181, 249);
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

    const style = (win() as HTMLElement).style;
    expect([style.left, style.top]).toEqual(["208px", "249px"]);
  });

  it("stays on the page when the control is near the bottom of it", async () => {
    const rect = (right: number, top: number) => ({ right, top }) as DOMRect;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.hasAttribute("aria-haspopup") ? rect(181, window.innerHeight - 20) : rect(0, 0);
    });
    open([A, B, C, D, E]);
    await waitFor(() => expect(names()).toEqual(["가 팀", "나 팀", "다 팀"]));

    fireEvent.click(more() as HTMLButtonElement);

    // Five teams: about 198px of window, and 8px kept clear under it.
    const top = Number.parseInt((win() as HTMLElement).style.top, 10);
    expect(top).toBe(window.innerHeight - 198 - 8);
  });
});

describe("TeamMenu, pinning in the window", () => {
  it("pins a team there, and the sidebar and a screen's row take the new order", async () => {
    // The user, 2026-10-06: "맨위 고정도 거기로 이동". The pin is on the
    // account; the answer is the list in its new order.
    pin.mockResolvedValue([{ ...E, pinned: true }, A, B, C, D]);
    open([A, B, C, D, E], true);
    await waitFor(() => expect(shown()).toBe("team_a"));
    fireEvent.click(more() as HTMLButtonElement);
    expect(pinButton("마 팀").textContent).toBe("고정");
    expect(pinButton("마 팀").getAttribute("aria-label")).toBe("마 팀 맨 위에 고정");

    fireEvent.click(pinButton("마 팀"));

    await waitFor(() => expect(inWindow()).toEqual(["마 팀", "가 팀", "나 팀", "다 팀", "라 팀"]));
    expect(pin).toHaveBeenCalledExactlyOnceWith("team_e");
    // The window stays open: a person may want to pin another.
    expect(pinButton("마 팀").textContent).toBe("고정 해제");
    // The sidebar: the pinned team first; the team on show still among the three.
    expect(names()).toEqual(["마 팀", "가 팀", "나 팀"]);
    // Pinning changed nobody's choice of team.
    expect(chosen()).toEqual(["가 팀"]);
    expect(shown()).toBe("team_a");
    // The screen's row heard it as well.
    expect(screen.getByRole("button", { name: "마 팀 · 고정" })).toBeTruthy();
  });

  it("takes a pin off", async () => {
    unpin.mockResolvedValue([A, B, C]);
    open([{ ...C, pinned: true }, A, B]);
    await waitFor(() => expect(names()).toEqual(["다 팀", "가 팀", "나 팀"]));
    fireEvent.click(more() as HTMLButtonElement);

    fireEvent.click(pinButton("다 팀"));

    await waitFor(() => expect(names()).toEqual(["가 팀", "나 팀", "다 팀"]));
    expect(unpin).toHaveBeenCalledExactlyOnceWith("team_c");
    expect(pin).not.toHaveBeenCalled();
  });

  it("says to unpin one first when a fourth pin is refused, and changes nothing", async () => {
    pin.mockRejectedValue(new ApiError(409, "too_many_pinned_teams", "at most 3"));
    const teams = [{ ...A, pinned: true }, { ...B, pinned: true }, { ...C, pinned: true }, D];
    open(teams);
    await waitFor(() => expect(names()).toEqual(["가 팀", "나 팀", "다 팀"]));
    fireEvent.click(more() as HTMLButtonElement);

    fireEvent.click(pinButton("라 팀"));

    expect((await screen.findByRole("alert")).textContent).toContain("3개까지 고정할 수 있습니다");
    expect(inWindow()).toEqual(["가 팀", "나 팀", "다 팀", "라 팀"]);
    expect(pinButton("라 팀").textContent).toBe("고정");
  });

  it("a pin made in a screen's row reorders the sidebar too", async () => {
    pin.mockResolvedValue([{ ...B, pinned: true }, A, C, D]);
    window.localStorage.setItem("autune.team", "team_b");
    open([A, B, C, D], true);
    await waitFor(() => expect(shown()).toBe("team_b"));

    fireEvent.click(screen.getByRole("button", { name: "맨 위에 고정" }));

    await waitFor(() => expect(names()).toEqual(["나 팀", "가 팀", "다 팀"]));
  });
});

describe("TeamMenu, inside a meeting", () => {
  it.each(["/meetings/mtg_1", "/meetings/mtg_1/actions", "/meetings/mtg_1/summary"])(
    "on %s another team pressed goes to that team's meetings",
    async (path) => {
      // The user, 2026-10-06: "회의 상태에서 사이드바에 다른 팀 누르면 해당 팀의
      // 회의로 이동". Home shows the team named in its address.
      pathname.mockImplementation(() => path);
      open([A, B, C]);
      await waitFor(() => expect(chosen()).toEqual(["가 팀"]));

      fireEvent.click(entry("나 팀"));

      expect(window.localStorage.getItem("autune.team")).toBe("team_b");
      expect(push).toHaveBeenCalledExactlyOnceWith("/?team=team_b");
    },
  );

  it("the team already marked goes nowhere", async () => {
    pathname.mockImplementation(() => "/meetings/mtg_1");
    open([A, B, C]);
    await waitFor(() => expect(chosen()).toEqual(["가 팀"]));

    fireEvent.click(entry("가 팀"));

    expect(push).not.toHaveBeenCalled();
  });

  it("a team picked from the window goes there as well", async () => {
    pathname.mockImplementation(() => "/meetings/mtg_1/gap");
    open([A, B, C, D, E]);
    await waitFor(() => expect(chosen()).toEqual(["가 팀"]));
    fireEvent.click(more() as HTMLButtonElement);

    fireEvent.click(windowEntry("마 팀"));

    expect(push).toHaveBeenCalledExactlyOnceWith("/?team=team_e");
    expect(window.localStorage.getItem("autune.team")).toBe("team_e");
  });

  it("a pin made from a meeting's screen goes nowhere", async () => {
    pin.mockResolvedValue([{ ...B, pinned: true }, A, C]);
    pathname.mockImplementation(() => "/meetings/mtg_1");
    open([A, B, C]);
    await waitFor(() => expect(chosen()).toEqual(["가 팀"]));
    fireEvent.click(more() as HTMLButtonElement);

    fireEvent.click(pinButton("나 팀"));

    await waitFor(() => expect(names()).toEqual(["나 팀", "가 팀", "다 팀"]));
    expect(push).not.toHaveBeenCalled();
    expect(chosen()).toEqual(["가 팀"]);
  });

  it.each(["/meetings/new", "/", "/actions", "/dashboard"])(
    "on %s the choice changes and the page stays",
    async (path) => {
      // "회의 시작" may hold a recording or an upload in progress: a press in
      // the sidebar must not drop it. Elsewhere the screens follow by themselves.
      pathname.mockImplementation(() => path);
      open([A, B, C]);
      await waitFor(() => expect(chosen()).toEqual(["가 팀"]));

      fireEvent.click(entry("나 팀"));

      expect(chosen()).toEqual(["나 팀"]);
      expect(push).not.toHaveBeenCalled();
    },
  );
});
