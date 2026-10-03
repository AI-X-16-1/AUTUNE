import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkspaceScreen } from "./WorkspaceScreen";

// S02 after #552: the workspace is made with its creator alone, and then the
// screen offers invitation links. Nobody is added by making the workspace.

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const createTeam = vi.fn<(body: { name: string; role?: string }) => Promise<{ team_id: string; name: string }>>();
const inviteToTeam = vi.fn<(teamId: string, email: string) => Promise<{ token: string; expires_at: string }>>();
vi.mock("../api", () => ({
  createTeam: (body: { name: string; role?: string }) => createTeam(body),
  inviteToTeam: (teamId: string, email: string) => inviteToTeam(teamId, email),
}));

afterEach(() => {
  cleanup();
  replace.mockReset();
  createTeam.mockReset();
  inviteToTeam.mockReset();
});

async function create() {
  createTeam.mockResolvedValue({ team_id: "team_new", name: "검색 스쿼드" });
  render(<WorkspaceScreen />);
  fireEvent.change(screen.getByPlaceholderText("예: 검색 스쿼드"), { target: { value: "검색 스쿼드" } });
  fireEvent.click(screen.getByRole("button", { name: "만들기" }));
  await screen.findByRole("heading", { name: "팀원 초대" });
}

describe("WorkspaceScreen", () => {
  it("offers invitations for the workspace it just made, and sends no address with it", async () => {
    await create();

    expect(createTeam).toHaveBeenCalledExactlyOnceWith({ name: "검색 스쿼드" });
    expect(screen.getByText(/지금은 나만 들어가 있습니다/)).toBeTruthy();
    expect(replace).not.toHaveBeenCalled();

    inviteToTeam.mockResolvedValue({ token: "tok_abc", expires_at: "2026-10-09T10:00:00Z" });
    fireEvent.change(screen.getByLabelText("초대할 이메일 주소"), { target: { value: "a@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "초대 링크 만들기" }));

    await screen.findByLabelText("a@example.com 초대 링크");
    expect(inviteToTeam).toHaveBeenCalledExactlyOnceWith("team_new", "a@example.com");
  });

  it("lets the invite step be skipped", async () => {
    await create();

    fireEvent.click(screen.getByRole("button", { name: "시작하기" }));

    expect(replace).toHaveBeenCalledWith("/");
    expect(inviteToTeam).not.toHaveBeenCalled();
  });
});
