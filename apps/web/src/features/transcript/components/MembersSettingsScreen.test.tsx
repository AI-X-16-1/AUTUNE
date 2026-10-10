import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import type { PendingInvitation } from "../api";
import { MembersSettingsScreen } from "./MembersSettingsScreen";

// A team is what a project gets today: its own members and meetings. Someone
// already on one reaches S02 again from here -- and, since the follow-ups left
// on #552, sees what invitations are still out, takes one back, and can leave.
// The one person left on a team cannot leave it, and is offered its deletion
// (#1007).

type Team = { team_id: string; name: string };
const pendingList = vi.fn<(teamId: string) => Promise<PendingInvitation[]>>();
const cancel = vi.fn<(teamId: string, id: number) => Promise<PendingInvitation[]>>();
const leave = vi.fn<(teamId: string) => Promise<Team[]>>();
const remove = vi.fn<(teamId: string, name: string) => Promise<Team[]>>();
const membersOf = vi.fn<(teamId: string) => Promise<{ user_id: string; name: string }[]>>();
const invite = vi.fn<(teamId: string, email: string) => Promise<{ token: string; expires_at: string }>>();
vi.mock("../api", () => ({
  listTeams: () => Promise.resolve([{ team_id: "team_1", name: "Alpha" }]),
  listTeamMembers: (teamId: string) => membersOf(teamId),
  inviteToTeam: (teamId: string, email: string) => invite(teamId, email),
  listPendingInvitations: (teamId: string) => pendingList(teamId),
  cancelInvitation: (teamId: string, id: number) => cancel(teamId, id),
  leaveTeam: (teamId: string) => leave(teamId),
  deleteTeam: (teamId: string, name: string) => remove(teamId, name),
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
const ME = { user_id: "user_1", name: "민호" };
const MATE = { user_id: "user_2", name: "서연" };

beforeEach(() => {
  pendingList.mockResolvedValue([]);
  // A team of two: the screen as it was before #1007.
  membersOf.mockResolvedValue([ME, MATE]);
  vi.stubGlobal("location", { ...window.location, assign });
});

afterEach(() => {
  cleanup();
  for (const mock of [pendingList, cancel, leave, remove, membersOf, invite, remember, assign])
    mock.mockReset();
  vi.unstubAllGlobals();
});

const pendingSection = () => screen.findByRole("region", { name: "수락을 기다리는 초대" });
const leaveButton = () => screen.findByRole("button", { name: "이 팀에서 나가기" });
const deleteButton = () => screen.findByRole("button", { name: "이 팀 삭제" });
const nameField = () => screen.getByLabelText(/이 팀의 이름을 입력해 주세요/);
/** Alone on the team, with what deletion means open. */
const askToDelete = async () => {
  membersOf.mockResolvedValue([ME]);
  render(<MembersSettingsScreen />);
  fireEvent.click(await deleteButton());
};

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

  it("lists a link made for no address as that, with its own cancel", async () => {
    // #552: such a link admits whoever opens it, so the team sees it here
    // and any member can take it back.
    pendingList.mockResolvedValue([{ ...OUT, id: 9, email: null }]);
    cancel.mockResolvedValue([]);
    render(<MembersSettingsScreen />);

    const section = await pendingSection();
    expect(within(section).getByText("주소 없는 링크")).toBeTruthy();
    // It lasts an hour: the time it stops working, not a date.
    const at = new Date(OUT.expires_at);
    const pad = (n: number) => String(n).padStart(2, "0");
    expect(
      within(section).getByText(
        `서연 님이 초대 · ${pad(at.getHours())}:${pad(at.getMinutes())}까지, 한 번만`,
      ),
    ).toBeTruthy();
    fireEvent.click(within(section).getByRole("button", { name: "주소 없는 링크 초대 취소" }));

    await waitFor(() => expect(cancel).toHaveBeenCalledWith("team_1", 9));
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
    // The right that does not go with the membership is said too.
    expect(screen.getByText(/나간 뒤에도 설정의/)).toBeTruthy();
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

  it("tells the last member why they cannot leave, goes nowhere, and offers the deletion", async () => {
    // The list on screen was read while a mate was still on the team.
    leave.mockRejectedValue(new ApiError(409, "last_team_member", "the last member"));
    render(<MembersSettingsScreen />);

    fireEvent.click(await leaveButton());
    fireEvent.click(await leaveButton());

    const said = (await screen.findByRole("alert")).textContent;
    expect(said).toContain("남은 구성원이 나뿐이라");
    expect(said).toContain("팀을 삭제할 수 있습니다");
    expect(assign).not.toHaveBeenCalled();
    // The section is the deletion now, closed: nothing is typed or sent yet.
    expect(await deleteButton()).toBeTruthy();
    expect(screen.queryByRole("button", { name: "이 팀에서 나가기" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: /이 팀의 이름/ })).toBeNull();
    expect(remove).not.toHaveBeenCalled();
  });

  it("says any other failure to leave as that, and still offers leaving", async () => {
    leave.mockRejectedValue(new ApiError(500, "internal_error", "boom"));
    render(<MembersSettingsScreen />);

    fireEvent.click(await leaveButton());
    fireEvent.click(await leaveButton());

    expect((await screen.findByRole("alert")).textContent).toContain("나가지 못했습니다");
    expect(screen.queryByRole("button", { name: "이 팀 삭제" })).toBeNull();
    expect(await leaveButton()).toBeTruthy();
  });
});

describe("MembersSettingsScreen, for the one person left on the team (#1007)", () => {
  it("offers deleting the team in place of leaving it", async () => {
    membersOf.mockResolvedValue([ME]);
    render(<MembersSettingsScreen />);

    expect(await deleteButton()).toBeTruthy();
    expect(screen.getByRole("heading", { name: "팀 삭제" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "팀 나가기" })).toBeNull();
    expect(screen.queryByRole("button", { name: "이 팀에서 나가기" })).toBeNull();
  });

  it("offers a team of two what it always did: leaving, and no deletion", async () => {
    render(<MembersSettingsScreen />);

    expect(await leaveButton()).toBeTruthy();
    expect(screen.getByRole("heading", { name: "팀 나가기" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "이 팀 삭제" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "팀 삭제" })).toBeNull();
  });

  it("does not offer the deletion on a member list it could not read", async () => {
    membersOf.mockRejectedValue(new Error("offline"));
    render(<MembersSettingsScreen />);

    expect(await screen.findByText("구성원 목록을 불러오지 못했습니다.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "이 팀 삭제" })).toBeNull();
    expect(await leaveButton()).toBeTruthy();
  });

  it("says what goes and what stays in the team's tools before the name can be typed", async () => {
    await askToDelete();

    // What goes: everything in Autune, other people's lines among it.
    expect(screen.getByText(/모든 회의, 전사, 할 일, 결정 사항/)).toBeTruthy();
    expect(screen.getByText(/먼저 팀에서 나간 사람들이 이 팀 회의에서 한 말도 함께\s+삭제/)).toBeTruthy();
    expect(screen.getByText(/그 사람들에게 알림은 가지 않습니다/)).toBeTruthy();
    expect(screen.getByText(/되돌릴 수 없습니다/)).toBeTruthy();
    // What stays: B's copies, C's channel notices, E's channel reports.
    const stays = screen.getByText(/팀의 도구에 이미 보낸 것은 삭제되지 않고/).textContent ?? "";
    expect(stays).toMatch(/프로젝트별 회의록/);
    expect(stays).toMatch(/Notion 페이지와 Jira\s+이슈/);
    expect(stays).toMatch(/갭 질문과 다음 회의 안내/);
    expect(stays).toMatch(/회의 리포트와 주간 팀 리포트/);
    expect(stays).toMatch(/Autune에서는 이것들을 더 지울 수 없으니/);
    // And it comes before the field.
    const field = nameField();
    expect(
      screen.getByText(/팀의 도구에 이미 보낸 것은/).compareDocumentPosition(field) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(remove).not.toHaveBeenCalled();
  });

  it("sends nothing until a name has been typed", async () => {
    await askToDelete();
    const press = screen.getByRole("button", { name: "이 팀 삭제" }) as HTMLButtonElement;

    expect(press.disabled).toBe(true);
    fireEvent.change(nameField(), { target: { value: "   " } });
    expect(press.disabled).toBe(true);
    fireEvent.submit(nameField().closest("form") as HTMLFormElement);
    expect(remove).not.toHaveBeenCalled();

    fireEvent.change(nameField(), { target: { value: "Alpha" } });
    expect(press.disabled).toBe(false);
    expect(remove).not.toHaveBeenCalled();
  });

  it("can be put away, and what was typed is not kept", async () => {
    await askToDelete();
    fireEvent.change(nameField(), { target: { value: "Alpha" } });

    fireEvent.click(screen.getByRole("button", { name: "취소" }));

    expect(screen.queryByText(/되돌릴 수 없습니다/)).toBeNull();
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(await deleteButton());
    expect((nameField() as HTMLInputElement).value).toBe("");
  });

  it("sends the name as typed and goes to a team the person is still on", async () => {
    remove.mockResolvedValue([{ team_id: "team_2", name: "Beta" }]);
    await askToDelete();

    fireEvent.change(nameField(), { target: { value: " Alpha" } });
    fireEvent.click(screen.getByRole("button", { name: "이 팀 삭제" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    // The server decides what counts as the same name; nothing is trimmed here.
    expect(remove).toHaveBeenCalledTimes(1);
    expect(remove).toHaveBeenCalledWith("team_1", " Alpha");
    expect(remember).toHaveBeenCalledWith("team_2");
    expect(leave).not.toHaveBeenCalled();
  });

  it("goes to making a team when that was the only one", async () => {
    remove.mockResolvedValue([]);
    await askToDelete();

    fireEvent.change(nameField(), { target: { value: "Alpha" } });
    fireEvent.submit(nameField().closest("form") as HTMLFormElement);

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/workspace/new"));
    expect(remember).not.toHaveBeenCalled();
  });

  it.each([
    ["team_name_mismatch", 422, "입력한 이름이 팀 이름과 다릅니다"],
    ["team_meeting_in_progress", 409, "전사 중이거나 실시간으로 진행 중인 회의가 있어"],
  ])("says why on %s, keeps what was typed and goes nowhere", async (code, status, words) => {
    remove.mockRejectedValue(new ApiError(status, code, "refused"));
    await askToDelete();

    fireEvent.change(nameField(), { target: { value: "Alpah" } });
    fireEvent.click(screen.getByRole("button", { name: "이 팀 삭제" }));

    expect((await screen.findByRole("alert")).textContent).toContain(words);
    expect(assign).not.toHaveBeenCalled();
    expect(remember).not.toHaveBeenCalled();
    // Still open, to correct the name or try again.
    expect((nameField() as HTMLInputElement).value).toBe("Alpah");
    expect((screen.getByRole("button", { name: "이 팀 삭제" }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it("goes back to leaving when somebody had joined, and reads the members again", async () => {
    remove.mockRejectedValue(new ApiError(409, "team_has_other_members", "refused"));
    await askToDelete();
    membersOf.mockResolvedValue([ME, MATE]);

    fireEvent.change(nameField(), { target: { value: "Alpha" } });
    fireEvent.click(screen.getByRole("button", { name: "이 팀 삭제" }));

    expect((await screen.findByRole("alert")).textContent).toContain("다른 구성원이 있어");
    expect(await leaveButton()).toBeTruthy();
    expect(await screen.findByText("서연")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "이 팀 삭제" })).toBeNull();
    expect(membersOf).toHaveBeenCalledTimes(2);
    expect(assign).not.toHaveBeenCalled();
  });

  it("does not say nothing was deleted when the request did not come back", async () => {
    remove.mockRejectedValue(new TypeError("Failed to fetch"));
    await askToDelete();

    fireEvent.change(nameField(), { target: { value: "Alpha" } });
    fireEvent.click(screen.getByRole("button", { name: "이 팀 삭제" }));

    const said = (await screen.findByRole("alert")).textContent ?? "";
    expect(said).toBe("팀을 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    expect(assign).not.toHaveBeenCalled();
  });
});
