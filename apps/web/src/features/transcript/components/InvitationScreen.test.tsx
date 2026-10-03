import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InvitationScreen } from "./InvitationScreen";

// Where an invitation link lands (#552). The person joins by pressing a
// button, signed in; the token never stays in the address bar; a refusal says
// nothing about the invitation.

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const session = vi.fn<() => Promise<{ id: string; email: string } | null>>();
const setSignedIn = vi.fn();
vi.mock("@/shared/api/auth", () => ({ getSession: () => session() }));
vi.mock("@/shared/api/client", () => ({ setSignedIn: (value: boolean) => setSignedIn(value) }));

const accept = vi.fn<(token: string) => Promise<{ team_id: string; name: string }>>();
vi.mock("../api", () => ({ acceptInvitation: (token: string) => accept(token) }));

const ME = { id: "user_1", email: "newcomer@example.com" };
const KEY = "autune.invitation";

function open(hash = "#tok_abc") {
  window.history.replaceState(null, "", `/invite${hash}`);
  render(<InvitationScreen signIn={<div>로그인 카드</div>} />);
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  replace.mockReset();
  session.mockReset();
  setSignedIn.mockReset();
  accept.mockReset();
});

describe("InvitationScreen", () => {
  it("takes the token out of the address bar and joins nothing by itself", async () => {
    session.mockResolvedValue(ME);

    open();

    await screen.findByRole("button", { name: "초대 수락" });
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/invite");
    expect(accept).not.toHaveBeenCalled();
    expect(screen.getByText("지금 로그인한 계정: newcomer@example.com")).toBeTruthy();
    expect(setSignedIn).toHaveBeenCalledWith(true);
  });

  it("joins when the person accepts, and forgets the token", async () => {
    session.mockResolvedValue(ME);
    accept.mockResolvedValue({ team_id: "team_1", name: "검색 스쿼드" });
    open();

    fireEvent.click(await screen.findByRole("button", { name: "초대 수락" }));

    expect((await screen.findByRole("status")).textContent).toBe(
      "검색 스쿼드 워크스페이스에 참여했습니다.",
    );
    expect(accept).toHaveBeenCalledExactlyOnceWith("tok_abc");
    expect(window.sessionStorage.getItem(KEY)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "시작하기" }));
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("offers sign-in to a signed-out visitor and keeps the token for the way back", async () => {
    session.mockResolvedValue(null);

    open();

    expect(await screen.findByText("로그인 카드")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "초대 수락" })).toBeNull();
    expect(window.sessionStorage.getItem(KEY)).toBe("tok_abc");
    expect(window.location.hash).toBe("");
  });

  it("finds the token again after sign-in brought the person back without it", async () => {
    window.sessionStorage.setItem(KEY, "tok_kept");
    session.mockResolvedValue(ME);
    accept.mockResolvedValue({ team_id: "team_1", name: "검색 스쿼드" });

    open("");
    fireEvent.click(await screen.findByRole("button", { name: "초대 수락" }));

    await waitFor(() => expect(accept).toHaveBeenCalledExactlyOnceWith("tok_kept"));
  });

  it("says one thing for every refusal, and which account is signed in", async () => {
    session.mockResolvedValue(ME);
    accept.mockRejectedValue(new Error("this invitation cannot be used"));
    open();

    fireEvent.click(await screen.findByRole("button", { name: "초대 수락" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("이 초대 링크는 쓸 수 없습니다");
    expect(alert.textContent).not.toContain("cannot be used");
    expect(screen.getByText("지금 로그인한 계정: newcomer@example.com")).toBeTruthy();
    expect(window.sessionStorage.getItem(KEY)).toBeNull();
    expect(screen.queryByRole("button", { name: "초대 수락" })).toBeNull();
  });

  it("lets the person walk away without joining", async () => {
    session.mockResolvedValue(ME);
    open();

    fireEvent.click(await screen.findByRole("button", { name: "수락하지 않고 홈으로" }));

    expect(accept).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem(KEY)).toBeNull();
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("says so when there is no token at all", async () => {
    session.mockResolvedValue(ME);

    open("");

    expect(await screen.findByText(/초대 링크가 올바르지 않습니다/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "초대 수락" })).toBeNull();
  });
});
