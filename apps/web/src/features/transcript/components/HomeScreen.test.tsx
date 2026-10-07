import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HomeScreen } from "./HomeScreen";
import { rememberTeam } from "../selectedTeam";
import type { MeetingSummary, TeamSummary } from "../types";

// S05 (the user, 2026-10-06): home opens on the five latest meetings of every
// team a person is on; pressing a team shows that team's. The row lists three
// teams and "더보기", which opens the window the sidebar opens -- every team,
// and the pin.

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));

const listTeams = vi.fn<() => Promise<TeamSummary[]>>();
const listMeetings = vi.fn<(teamId?: string) => Promise<MeetingSummary[]>>();
const pin = vi.fn<(teamId: string) => Promise<TeamSummary[]>>();
vi.mock("../api", () => ({
  listTeams: () => listTeams(),
  listMeetings: (teamId?: string) => listMeetings(teamId),
  pinTeam: (teamId: string) => pin(teamId),
  unpinTeam: vi.fn(),
}));

// The invitation control is 설정 › 구성원's own and has its own tests
// (TeamInvite.test.tsx). Here it is enough to see WHICH team it is handed.
vi.mock("./TeamInvite", () => ({
  TeamInvite: ({ teamId, canConnectMail }: { teamId: string; canConnectMail?: boolean }) => (
    <div data-testid="team-invite" data-team={teamId} data-mail={String(canConnectMail === true)} />
  ),
}));

afterEach(() => {
  cleanup();
  for (const mock of [replace, listTeams, listMeetings, pin]) mock.mockReset();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});

const SEARCH: TeamSummary = { team_id: "team_search", name: "검색 스쿼드", pinned: true };
const PAY: TeamSummary = { team_id: "team_pay", name: "결제 스쿼드", pinned: false };
const OPS: TeamSummary = { team_id: "team_ops", name: "운영 스쿼드", pinned: false };
const DESIGN: TeamSummary = { team_id: "team_design", name: "디자인 스쿼드", pinned: false };
const SALES: TeamSummary = { team_id: "team_sales", name: "영업 스쿼드", pinned: false };

function meeting(id: string, title: string): MeetingSummary {
  return { meeting_id: id, title, status: "complete", started_at: "2026-10-01T01:00:00Z" };
}

const SEARCH_MEETINGS = [meeting("mtg_s1", "검색 주간 회의"), meeting("mtg_s2", "랭킹 리뷰")];
const PAY_MEETINGS = [meeting("mtg_p1", "결제 장애 회고")];
// What `GET /meetings` answers with no team: every team's, newest first.
const EVERY = [
  meeting("mtg_1", "첫째 회의"),
  meeting("mtg_2", "둘째 회의"),
  meeting("mtg_3", "셋째 회의"),
  meeting("mtg_4", "넷째 회의"),
  meeting("mtg_5", "다섯째 회의"),
  meeting("mtg_6", "여섯째 회의"),
  meeting("mtg_7", "일곱째 회의"),
];

function meetingsOf(teamId?: string): MeetingSummary[] {
  if (teamId === undefined) return EVERY;
  if (teamId === "team_search") return SEARCH_MEETINGS;
  if (teamId === "team_pay") return PAY_MEETINGS;
  return [];
}

function open(teams: TeamSummary[]) {
  listTeams.mockResolvedValue(teams);
  listMeetings.mockImplementation((teamId) => Promise.resolve(meetingsOf(teamId)));
  render(<HomeScreen />);
}

const row = () => screen.getByRole("group", { name: "팀" });
const chips = () =>
  [...row().querySelectorAll("button[aria-pressed]")]
    .filter((b) => !b.closest('[role="dialog"]'))
    .map((b) => b.textContent);
const pressed = () =>
  [...row().querySelectorAll('button[aria-pressed="true"]')]
    .filter((b) => !b.closest('[role="dialog"]'))
    .map((b) => b.textContent);
const chip = (name: string) =>
  [...row().querySelectorAll("button[aria-pressed]")].find(
    (b) => !b.closest('[role="dialog"]') && b.textContent === name,
  ) as HTMLButtonElement;
const win = () => screen.queryByRole("dialog", { name: "팀" });
const titles = () => [...document.querySelectorAll('a[href^="/meetings/"]')].map((a) => a.textContent);

describe("HomeScreen", () => {
  it("shows one team's meetings to somebody on one team, with no team row", async () => {
    open([SEARCH]);

    expect(await screen.findByText("검색 주간 회의")).toBeTruthy();
    expect(listMeetings).toHaveBeenCalledExactlyOnceWith("team_search");
    expect(screen.queryByRole("group", { name: "팀" })).toBeNull();
    expect(replace).not.toHaveBeenCalled();
  });

  it("opens on the five latest meetings of every team", async () => {
    // "홈에는 모든 팀의 최근 회의 5개가 보이고".
    open([SEARCH, PAY]);

    expect(await screen.findByText("첫째 회의")).toBeTruthy();
    expect(listMeetings).toHaveBeenCalledExactlyOnceWith(undefined);
    expect(titles()).toHaveLength(5);
    expect(screen.queryByText("여섯째 회의")).toBeNull();
    expect(screen.getByRole("heading", { name: "모든 팀의 최근 회의" })).toBeTruthy();
    expect(pressed()).toEqual(["전체"]);
  });

  it("shows a team's meetings when the team is pressed, and keeps that choice", async () => {
    // "팀을 누르면 팀의 최근 회의가 보이게".
    open([SEARCH, PAY]);
    await screen.findByText("첫째 회의");

    fireEvent.click(chip("결제 스쿼드"));

    expect(await screen.findByText("결제 장애 회고")).toBeTruthy();
    expect(screen.queryByText("첫째 회의")).toBeNull();
    expect(listMeetings).toHaveBeenLastCalledWith("team_pay");
    expect(screen.getByRole("heading", { name: "최근 회의" })).toBeTruthy();
    expect(pressed()).toEqual(["결제 스쿼드"]);
    // The sidebar's mark and the next team-level screen follow the same choice,
    // and a reload shows the same team.
    expect(window.localStorage.getItem("autune.team")).toBe("team_pay");
    expect(window.location.search).toBe("?team=team_pay");
  });

  it("goes back to every team with 전체, and leaves the kept choice alone", async () => {
    open([SEARCH, PAY]);
    await screen.findByText("첫째 회의");
    fireEvent.click(chip("결제 스쿼드"));
    await screen.findByText("결제 장애 회고");

    fireEvent.click(chip("전체"));

    expect(await screen.findByText("첫째 회의")).toBeTruthy();
    expect(pressed()).toEqual(["전체"]);
    expect(window.location.search).toBe("");
    expect(window.localStorage.getItem("autune.team")).toBe("team_pay");
  });

  it("opens on the team named in the address", async () => {
    // A reload, or the sidebar's move out of a meeting's screen.
    window.history.replaceState(null, "", "/?team=team_pay");

    open([SEARCH, PAY]);

    expect(await screen.findByText("결제 장애 회고")).toBeTruthy();
    expect(listMeetings).toHaveBeenLastCalledWith("team_pay");
    expect(pressed()).toEqual(["결제 스쿼드"]);
  });

  it("shows every team when the address names a team the person is not on", async () => {
    window.history.replaceState(null, "", "/?team=team_of_somebody_else");

    open([SEARCH, PAY]);

    expect(await screen.findByText("첫째 회의")).toBeTruthy();
    expect(listMeetings).not.toHaveBeenCalledWith("team_of_somebody_else");
    expect(pressed()).toEqual(["전체"]);
  });

  it("does not ask for a team that is chosen but is not the person's", async () => {
    // A choice kept in this browser from before they left that team.
    open([SEARCH, PAY]);
    await screen.findByText("첫째 회의");

    act(() => rememberTeam("team_left_last_week"));

    await waitFor(() => expect(pressed()).toEqual(["전체"]));
    expect(listMeetings).not.toHaveBeenCalledWith("team_left_last_week");
    expect(screen.getByText("첫째 회의")).toBeTruthy();
  });

  it("shows the team chosen in the sidebar while home is open", async () => {
    open([SEARCH, PAY]);
    await screen.findByText("첫째 회의");

    act(() => rememberTeam("team_search"));

    expect(await screen.findByText("검색 주간 회의")).toBeTruthy();
    expect(pressed()).toEqual(["검색 스쿼드 · 고정"]);
  });

  it("keeps the team row when the chosen team has no meetings yet", async () => {
    open([SEARCH, PAY, OPS]);
    await screen.findByText("첫째 회의");

    fireEvent.click(chip("운영 스쿼드"));

    // The first-meeting screen, for this team -- and a way to the others.
    expect(await screen.findByRole("button", { name: "파일 선택" })).toBeTruthy();
    fireEvent.click(chip("검색 스쿼드 · 고정"));
    expect(await screen.findByText("검색 주간 회의")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "파일 선택" })).toBeNull();
  });

  it("shows the first-meeting screen when no team has a meeting", async () => {
    listTeams.mockResolvedValue([SEARCH, PAY]);
    listMeetings.mockResolvedValue([]);
    render(<HomeScreen />);

    expect(await screen.findByRole("button", { name: "파일 선택" })).toBeTruthy();
    expect(chips()).toEqual(["전체", "검색 스쿼드 · 고정", "결제 스쿼드"]);
  });

  it("drops a late answer for the view that was left", async () => {
    listTeams.mockResolvedValue([SEARCH, PAY]);
    let late: (rows: MeetingSummary[]) => void = () => {};
    listMeetings.mockImplementation((teamId) =>
      teamId === undefined
        ? new Promise((resolve) => {
            late = resolve;
          })
        : Promise.resolve(PAY_MEETINGS),
    );
    render(<HomeScreen />);
    await waitFor(() => expect(listMeetings).toHaveBeenCalledWith(undefined));
    fireEvent.click(chip("결제 스쿼드"));
    await screen.findByText("결제 장애 회고");

    late(EVERY);

    await waitFor(() => expect(screen.getByText("결제 장애 회고")).toBeTruthy());
    expect(screen.queryByText("첫째 회의")).toBeNull();
  });

  it("sends somebody on no team to make a workspace, and asks for no meetings", async () => {
    listTeams.mockResolvedValue([]);
    render(<HomeScreen />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/workspace/new"));
    expect(listMeetings).not.toHaveBeenCalled();
  });
});

describe("HomeScreen, the team row", () => {
  it("lists three teams and 더보기, and has no pin button of its own", async () => {
    // "홈화면에서 팀목록 3개만 나오게 하고 옆에 더보기를 배치 ... 맨위 고정도
    // 거기로 이동".
    open([SEARCH, PAY, OPS, DESIGN, SALES]);
    await screen.findByText("첫째 회의");

    expect(chips()).toEqual(["전체", "검색 스쿼드 · 고정", "결제 스쿼드", "운영 스쿼드"]);
    expect(screen.getByRole("button", { name: "팀 더보기" }).textContent).toBe("더보기");
    expect(screen.queryByRole("button", { name: "맨 위에 고정" })).toBeNull();
    expect(screen.queryByRole("button", { name: "고정 해제" })).toBeNull();
  });

  it("opens the window of every team from 더보기, and shows the team picked there", async () => {
    open([SEARCH, PAY, OPS, DESIGN, SALES]);
    await screen.findByText("첫째 회의");
    expect(win()).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "팀 더보기" }));

    expect(
      [...(win() as HTMLElement).querySelectorAll("button[aria-pressed]")].map((b) => b.textContent),
    ).toEqual(["검색 스쿼드", "결제 스쿼드", "운영 스쿼드", "디자인 스쿼드", "영업 스쿼드"]);
    fireEvent.click(
      [...(win() as HTMLElement).querySelectorAll("button[aria-pressed]")].find(
        (b) => b.textContent === "영업 스쿼드",
      ) as HTMLElement,
    );

    expect(win()).toBeNull();
    // Not one of the first three: it takes the last place of the row.
    await waitFor(() =>
      expect(chips()).toEqual(["전체", "검색 스쿼드 · 고정", "결제 스쿼드", "영업 스쿼드"]),
    );
    expect(pressed()).toEqual(["영업 스쿼드"]);
    expect(listMeetings).toHaveBeenLastCalledWith("team_sales");
  });

  it("pins from that window, and the row takes the new order without changing what is shown", async () => {
    pin.mockResolvedValue([SEARCH, { ...SALES, pinned: true }, PAY, OPS, DESIGN]);
    open([SEARCH, PAY, OPS, DESIGN, SALES]);
    await screen.findByText("첫째 회의");
    fireEvent.click(screen.getByRole("button", { name: "팀 더보기" }));

    fireEvent.click(screen.getByRole("button", { name: "영업 스쿼드 맨 위에 고정" }));

    await waitFor(() =>
      expect(chips()).toEqual(["전체", "검색 스쿼드 · 고정", "영업 스쿼드 · 고정", "결제 스쿼드"]),
    );
    expect(pin).toHaveBeenCalledExactlyOnceWith("team_sales");
    expect(pressed()).toEqual(["전체"]);
    expect(screen.getByText("첫째 회의")).toBeTruthy();
    // Still open: a person may want to pin another.
    expect(win()).not.toBeNull();
  });
});

// "팀원 추가" at the right end of the row, opening a window in the middle of
// the screen (the user, 2026-10-07). It is 설정 › 구성원's invitation, for the
// one team the home screen is showing.
describe("HomeScreen, 팀원 추가", () => {
  const addButton = () => screen.queryByRole("button", { name: "팀원 추가" });
  const windowOpen = () => screen.queryByRole("dialog", { name: "팀원 추가" });
  const invitedTeam = () => screen.getByTestId("team-invite").getAttribute("data-team");

  it("is not offered while 전체 is shown: there is no one team to invite to", async () => {
    open([SEARCH, PAY]);
    await screen.findByText("첫째 회의");

    expect(addButton()).toBeNull();
  });

  it("is at the end of the row once a team is shown, and opens a window for that team", async () => {
    open([SEARCH, PAY]);
    await screen.findByText("첫째 회의");
    fireEvent.click(screen.getByRole("button", { name: "결제 스쿼드" }));
    await screen.findByText("결제 장애 회고");

    const button = addButton();
    expect(button).not.toBeNull();
    expect(row().contains(button)).toBe(true);
    expect(row().lastElementChild?.contains(button)).toBe(true);
    expect(windowOpen()).toBeNull();

    fireEvent.click(button!);

    const dialog = windowOpen()!;
    expect(dialog.getAttribute("aria-modal")).not.toBeNull();
    expect(dialog.textContent).toContain("결제 스쿼드 팀에 초대합니다.");
    expect(invitedTeam()).toBe("team_pay");
    // Over a dimmed page drawn on the body, not inside the row.
    const backdrop = screen.getByTestId("team-member-backdrop");
    expect(backdrop.parentElement).toBe(document.body);
    expect(row().contains(dialog)).toBe(false);
    // Connecting Gmail leaves the page; it stays under 설정 › 구성원.
    expect(screen.getByTestId("team-invite").getAttribute("data-mail")).toBe("false");
    expect(dialog.textContent).toContain("설정 › 구성원");
  });

  it("is for the team shown now, not the one shown before", async () => {
    open([SEARCH, PAY]);
    await screen.findByText("첫째 회의");
    fireEvent.click(screen.getByRole("button", { name: "결제 스쿼드" }));
    await screen.findByText("결제 장애 회고");
    fireEvent.click(screen.getByRole("button", { name: "검색 스쿼드 · 고정" }));
    await screen.findByText("검색 주간 회의");

    fireEvent.click(addButton()!);

    expect(windowOpen()!.textContent).toContain("검색 스쿼드 팀에 초대합니다.");
    expect(invitedTeam()).toBe("team_search");
  });

  it("goes away with the team when 전체 is pressed again", async () => {
    open([SEARCH, PAY]);
    await screen.findByText("첫째 회의");
    fireEvent.click(screen.getByRole("button", { name: "결제 스쿼드" }));
    await screen.findByText("결제 장애 회고");
    expect(addButton()).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "전체" }));
    await screen.findByText("첫째 회의");

    expect(addButton()).toBeNull();
  });

  it("gives somebody on one team the button alone, for that team, and still no row", async () => {
    open([SEARCH]);
    await screen.findByText("검색 주간 회의");

    expect(screen.queryByRole("group", { name: "팀" })).toBeNull();
    fireEvent.click(addButton()!);

    expect(windowOpen()!.textContent).toContain("검색 스쿼드 팀에 초대합니다.");
    expect(invitedTeam()).toBe("team_search");
  });

  it.each(["닫기", "Escape", "the dimmed page", "the button again"])(
    "closes on %s",
    async (how) => {
      open([SEARCH]);
      await screen.findByText("검색 주간 회의");
      fireEvent.click(addButton()!);
      expect(windowOpen()).not.toBeNull();

      if (how === "닫기") fireEvent.click(screen.getByRole("button", { name: "닫기" }));
      else if (how === "Escape") fireEvent.keyDown(document, { key: "Escape" });
      else if (how === "the dimmed page")
        fireEvent.mouseDown(screen.getByTestId("team-member-backdrop"));
      else {
        // A press on the opener is the opener's: it is not also a press outside.
        fireEvent.mouseDown(addButton()!);
        fireEvent.click(addButton()!);
      }

      expect(windowOpen()).toBeNull();
      expect(screen.getByText("검색 주간 회의")).toBeTruthy();
    },
  );

  it("stays open on a press inside it", async () => {
    open([SEARCH]);
    await screen.findByText("검색 주간 회의");
    fireEvent.click(addButton()!);

    fireEvent.mouseDown(screen.getByTestId("team-invite"));

    expect(windowOpen()).not.toBeNull();
  });
});
