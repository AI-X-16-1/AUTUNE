import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import type { PendingInvitation } from "../api";
import { MembersSettingsScreen } from "./MembersSettingsScreen";

// A team is what a project gets today: its own members and meetings. Someone
// already on one reaches S02 again from here -- and, since the follow-ups left
// on #552, sees what invitations are still out, takes one back, and can leave.

type Team = { team_id: string; name: string };
const pendingList = vi.fn<(teamId: string) => Promise<PendingInvitation[]>>();
const cancel = vi.fn<(teamId: string, id: number) => Promise<PendingInvitation[]>>();
const leave = vi.fn<(teamId: string) => Promise<Team[]>>();
const invite = vi.fn<(teamId: string, email: string) => Promise<{ token: string; expires_at: string }>>();
vi.mock("../api", () => ({
  listTeams: () => Promise.resolve([{ team_id: "team_1", name: "Alpha" }]),
  listTeamMembers: () => Promise.resolve([{ user_id: "user_1", name: "민호" }]),
  inviteToTeam: (teamId: string, email: string) => invite(teamId, email),
  listPendingInvitations: (teamId: string) => pendingList(teamId),
  cancelInvitation: (teamId: string, id: number) => cancel(teamId, id),
  leaveTeam: (teamId: string) => leave(teamId),
}));
vi.mock("@/shared/api/auth", () => ({
  getGmailConnection: () => Promise.resolve(null),
  googleGmailConnectUrl: () => "",
  disconnectGmail: vi.fn(),
}));
const remember = vi.fn<(teamId: string) => void>();
vi.mock("../selectedTeam", async (original) => ({
  ...(await original<typeof import("../selectedTeam")>()),
  rememberTeam: (teamId: string) => remember(teamId),
}));

const OUT: PendingInvitation = {
  id: 7,
  email: "newcomer@example.com",
  expires_at: "2026-10-13T03:00:00Z",
  invited_by_name: "서연",
};
const assign = vi.fn<(to: string) => void>();

beforeEach(() => {
  pendingList.mockResolvedValue([]);
  vi.stubGlobal("location", { ...window.location, assign });
});

afterEach(() => {
  cleanup();
  for (const mock of [pendingList, cancel, leave, invite, remember, assign]) mock.mockReset();
  vi.unstubAllGlobals();
});

const pendingSection = () => screen.findByRole("region", { name: "수락을 기다리는 초대" });
const leaveButton = () => screen.findByRole("button", { name: "이 팀에서 나가기" });

describe("MembersSettingsScreen", () => {
  it("links to making another team for a project with other members", () => {
    render(<MembersSettingsScreen />);

    const link = screen.getByRole("link", { name: "새 팀 만들기" });
    expect(link.getAttribute("href")).toBe("/workspace/new");
  });

  it("lists the invitations still out: the address, who invited, when it lapses", async () => {
    pendingList.mockResolvedValue([OUT]);
    render(<MembersSettingsScreen />);

    const section = await pendingSection();
    expect(pendingList).toHaveBeenCalledWith("team_1");
    expect(within(section).getByText("newcomer@example.com")).toBeTruthy();
    expect(within(section).getByText(/서연 님이 초대 · 10월 13일까지/)).toBeTruthy();
  });

  it("shows no such section when nothing is out, and none when it cannot ask", async () => {
    const { unmount } = render(<MembersSettingsScreen />);
    await leaveButton();
    await waitFor(() => expect(pendingList).toHaveBeenCalled());
    expect(screen.queryByText("수락을 기다리는 초대")).toBeNull();
    unmount();

    pendingList.mockReset();
    pendingList.mockRejectedValue(new Error("500"));
    render(<MembersSettingsScreen />);
    await leaveButton();
    await waitFor(() => expect(pendingList).toHaveBeenCalled());
    expect(screen.queryByText("수락을 기다리는 초대")).toBeNull();
  });

  it("takes an invitation back and shows the list the server answers with", async () => {
    pendingList.mockResolvedValue([OUT, { ...OUT, id: 8, email: "second@example.com" }]);
    cancel.mockResolvedValue([{ ...OUT, id: 8, email: "second@example.com" }]);
    render(<MembersSettingsScreen />);

    fireEvent.click(await screen.findByRole("button", { name: "newcomer@example.com 초대 취소" }));

    await waitFor(() => expect(screen.queryByText("newcomer@example.com")).toBeNull());
    expect(cancel).toHaveBeenCalledWith("team_1", 7);
    expect(screen.getByText("second@example.com")).toBeTruthy();
  });

  it("says so when an invitation could not be taken back, and keeps it listed", async () => {
    pendingList.mockResolvedValue([OUT]);
    cancel.mockRejectedValue(new Error("500"));
    render(<MembersSettingsScreen />);

    fireEvent.click(await screen.findByRole("button", { name: "newcomer@example.com 초대 취소" }));

    expect((await screen.findByRole("alert")).textContent).toContain("초대를 취소하지 못했습니다");
    expect(screen.getByText("newcomer@example.com")).toBeTruthy();
  });

  it("reads the list again after an invitation is made", async () => {
    invite.mockResolvedValue({ token: "tok", expires_at: OUT.expires_at });
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: { writeText: () => Promise.resolve() },
    });
    render(<MembersSettingsScreen />);
    await leaveButton();
    await waitFor(() => expect(pendingList).toHaveBeenCalledTimes(1));
    pendingList.mockResolvedValue([OUT]);

    fireEvent.change(screen.getByLabelText("초대할 이메일 주소"), {
      target: { value: "newcomer@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "초대 링크 복사" }));

    expect(await pendingSection()).toBeTruthy();
    expect(pendingList).toHaveBeenCalledTimes(2);
  });

  it("says what leaving means before anything is sent", async () => {
    render(<MembersSettingsScreen />);

    fireEvent.click(await leaveButton());

    expect(screen.getByText(/이 팀의 회의와 기록을 더 볼 수 없습니다/)).toBeTruthy();
    expect(screen.getByText(/내가 한 말과 내가\s+담당한 항목은 팀의 기록으로 남습니다/)).toBeTruthy();
    expect(leave).not.toHaveBeenCalled();

    // And it can be put away again.
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(screen.queryByText(/더 볼 수 없습니다/)).toBeNull();
    expect(leave).not.toHaveBeenCalled();
  });

  it("leaves on the second press and goes to a team the person is still on", async () => {
    leave.mockResolvedValue([{ team_id: "team_2", name: "Beta" }]);
    render(<MembersSettingsScreen />);

    fireEvent.click(await leaveButton());
    fireEvent.click(await leaveButton());

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    expect(leave).toHaveBeenCalledWith("team_1");
    expect(remember).toHaveBeenCalledWith("team_2");
  });

  it("goes to making a team when that was the only one", async () => {
    leave.mockResolvedValue([]);
    render(<MembersSettingsScreen />);

    fireEvent.click(await leaveButton());
    fireEvent.click(await leaveButton());

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/workspace/new"));
    expect(remember).not.toHaveBeenCalled();
  });

  it("tells the last member why they cannot leave, and goes nowhere", async () => {
    leave.mockRejectedValue(new ApiError(409, "last_team_member", "the last member"));
    render(<MembersSettingsScreen />);

    fireEvent.click(await leaveButton());
    fireEvent.click(await leaveButton());

    expect((await screen.findByRole("alert")).textContent).toContain("남은 구성원이 나뿐이라");
    expect(assign).not.toHaveBeenCalled();
  });
});
