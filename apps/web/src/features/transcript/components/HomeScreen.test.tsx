import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HomeScreen } from "./HomeScreen";
import type { MeetingSummary, TeamSummary } from "../types";

// S05 with one team at a time (the user, 2026-10-05): somebody on several
// teams saw every team's meetings in one list. The list is now the chosen
// team's, and the first team -- a pinned one, if they pinned any -- is chosen.

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));

const listTeams = vi.fn<() => Promise<TeamSummary[]>>();
const listMeetings = vi.fn<(teamId?: string) => Promise<MeetingSummary[]>>();
vi.mock("../api", () => ({
  listTeams: () => listTeams(),
  listMeetings: (teamId?: string) => listMeetings(teamId),
  pinTeam: vi.fn(),
  unpinTeam: vi.fn(),
}));

afterEach(() => {
  cleanup();
  replace.mockReset();
  listTeams.mockReset();
  listMeetings.mockReset();
});

const SEARCH = { team_id: "team_search", name: "검색 스쿼드", pinned: true };
const PAY = { team_id: "team_pay", name: "결제 스쿼드" };

function meeting(id: string, title: string): MeetingSummary {
  return { meeting_id: id, title, status: "complete", started_at: "2026-10-01T01:00:00Z" };
}

const SEARCH_MEETINGS = [meeting("mtg_s1", "검색 주간 회의"), meeting("mtg_s2", "랭킹 리뷰")];
const PAY_MEETINGS = [meeting("mtg_p1", "결제 장애 회고")];

/** What the server answers: each team's own, and nothing for no team named. */
function meetingsOf(teamId?: string): MeetingSummary[] {
  if (teamId === "team_search") return SEARCH_MEETINGS;
  if (teamId === "team_pay") return PAY_MEETINGS;
  return [];
}

describe("HomeScreen", () => {
  it("shows one team's meetings to somebody on one team, with no team row", async () => {
    listTeams.mockResolvedValue([SEARCH]);
    listMeetings.mockImplementation((teamId) => Promise.resolve(meetingsOf(teamId)));
    render(<HomeScreen />);

    expect(await screen.findByText("검색 주간 회의")).toBeTruthy();
    expect(listMeetings).toHaveBeenCalledExactlyOnceWith("team_search");
    expect(screen.queryByRole("button", { name: /검색 스쿼드/ })).toBeNull();
    expect(replace).not.toHaveBeenCalled();
  });

  it("shows the first team's meetings and only those to somebody on two", async () => {
    listTeams.mockResolvedValue([SEARCH, PAY]);
    listMeetings.mockImplementation((teamId) => Promise.resolve(meetingsOf(teamId)));
    render(<HomeScreen />);

    expect(await screen.findByText("검색 주간 회의")).toBeTruthy();
    expect(screen.getByText("랭킹 리뷰")).toBeTruthy();
    expect(screen.queryByText("결제 장애 회고")).toBeNull();
    expect(listMeetings).toHaveBeenCalledExactlyOnceWith("team_search");
    expect(listMeetings).not.toHaveBeenCalledWith(undefined);
  });

  it("asks for the other team's meetings when its chip is pressed", async () => {
    listTeams.mockResolvedValue([SEARCH, PAY]);
    listMeetings.mockImplementation((teamId) => Promise.resolve(meetingsOf(teamId)));
    render(<HomeScreen />);
    await screen.findByText("검색 주간 회의");

    fireEvent.click(screen.getByRole("button", { name: "결제 스쿼드" }));

    expect(await screen.findByText("결제 장애 회고")).toBeTruthy();
    expect(screen.queryByText("검색 주간 회의")).toBeNull();
    expect(listMeetings).toHaveBeenLastCalledWith("team_pay");
  });

  it("keeps the team row when the chosen team has no meetings yet", async () => {
    listTeams.mockResolvedValue([PAY, SEARCH]);
    listMeetings.mockImplementation((teamId) =>
      Promise.resolve(teamId === "team_pay" ? [] : meetingsOf(teamId)),
    );
    render(<HomeScreen />);

    // The first-meeting screen, for this team -- and a way to the other one.
    expect(await screen.findByRole("button", { name: "파일 선택" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "검색 스쿼드 · 고정" }));

    expect(await screen.findByText("검색 주간 회의")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "파일 선택" })).toBeNull();
  });

  it("drops a late answer for the team that was left", async () => {
    listTeams.mockResolvedValue([SEARCH, PAY]);
    let late: (rows: MeetingSummary[]) => void = () => {};
    listMeetings.mockImplementation((teamId) =>
      teamId === "team_search"
        ? new Promise((resolve) => {
            late = resolve;
          })
        : Promise.resolve(PAY_MEETINGS),
    );
    render(<HomeScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "결제 스쿼드" }));
    await screen.findByText("결제 장애 회고");

    late(SEARCH_MEETINGS);

    await waitFor(() => expect(screen.getByText("결제 장애 회고")).toBeTruthy());
    expect(screen.queryByText("검색 주간 회의")).toBeNull();
  });

  it("sends somebody on no team to make a workspace, and asks for no meetings", async () => {
    listTeams.mockResolvedValue([]);
    render(<HomeScreen />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/workspace/new"));
    expect(listMeetings).not.toHaveBeenCalled();
  });
});
