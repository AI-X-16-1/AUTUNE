import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppSidebar } from "./AppSidebar";

// Signing out from the sidebar: who is offered it, what it does, and what it
// does when the request does not get through.

const replace = vi.fn();
const logout = vi.fn();
const setSignedIn = vi.fn();
const sessionUser = vi.fn();

const pathname = vi.fn(() => "/");
vi.mock("next/navigation", () => ({
  usePathname: () => pathname(),
  useRouter: () => ({ replace }),
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("@/shared/api/auth", () => ({ logout: () => logout() }));
vi.mock("@/shared/api/client", () => ({ setSignedIn: (value: boolean) => setSignedIn(value) }));
vi.mock("./SessionGate", () => ({ useSessionUser: () => sessionUser() }));
// Module A's menu, which reads the person's teams; its own tests are beside it.
vi.mock("@/features/transcript", () => ({
  TeamMenu: () => <div data-testid="team-menu" />,
}));

const ME = { id: "user_me", email: "me@example.com", display_name: "Me", teams: [] };

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  pathname.mockImplementation(() => "/");
});

const signOutButton = () => screen.queryByRole("button", { name: "로그아웃" });

describe("AppSidebar, the team", () => {
  it("carries the team menu above the screens' own menu", () => {
    sessionUser.mockReturnValue(ME);
    render(<AppSidebar />);

    const menu = screen.getByTestId("team-menu");
    const nav = screen.getByRole("navigation", { name: "주요 메뉴" });
    expect(menu.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("AppSidebar, the screens", () => {
  const entries = () =>
    [...screen.getByRole("navigation", { name: "주요 메뉴" }).children].map((el) => el.textContent);
  const current = () =>
    [...document.querySelectorAll('[aria-current="page"]')].map((el) => el.textContent);

  it("has no 회의 entry: the meeting list is 홈", () => {
    // The user, 2026-10-06. It led where 홈 leads and differed only in when
    // it was lit. "회의 시작" above the menu is a button, and stays.
    sessionUser.mockReturnValue(ME);
    render(<AppSidebar />);

    expect(entries()).toEqual([
      "홈",
      "액션아이템",
      "갭 리포트",
      "결정 히스토리",
      "자료",
      "대시보드",
      "승인 대기",
    ]);
    expect(screen.getByText("회의 시작")).toBeTruthy();
  });

  it.each([
    ["/", "홈"],
    ["/meetings/mtg_1", "홈"],
    ["/meetings/mtg_1/actions", "홈"],
    ["/actions", "액션아이템"],
    ["/settings/members", "설정"],
  ])("on %s the lit entry is %s", (path, label) => {
    pathname.mockImplementation(() => path);
    sessionUser.mockReturnValue(ME);
    render(<AppSidebar />);

    expect(current()).toEqual([label]);
  });
});

describe("AppSidebar, the documents", () => {
  it.each([true, false])("links to the three documents of /legal (signed in: %s)", (signedIn) => {
    sessionUser.mockReturnValue(signedIn ? ME : null);
    render(<AppSidebar />);

    const links = [
      ...screen.getByRole("navigation", { name: "약관 및 정책" }).querySelectorAll("a"),
    ].map((a) => [a.textContent, a.getAttribute("href"), a.getAttribute("target")]);
    expect(links).toEqual([
      ["이용약관", "/legal#terms", "_blank"],
      ["개인정보 처리방침", "/legal#privacy", "_blank"],
      ["정보보호 정책", "/legal#security", "_blank"],
    ]);
  });
});

describe("AppSidebar, signing out", () => {
  it("offers 로그아웃 under the signed-in person's name", () => {
    sessionUser.mockReturnValue(ME);
    render(<AppSidebar />);

    expect(screen.getByText("me@example.com")).toBeTruthy();
    expect(signOutButton()).not.toBeNull();
  });

  it("offers nothing to a tab with no session: a developer token has none to end", () => {
    sessionUser.mockReturnValue(null);
    render(<AppSidebar />);

    expect(signOutButton()).toBeNull();
  });

  it("ends the session, stops calling as that person, and goes to the sign-in screen", async () => {
    sessionUser.mockReturnValue(ME);
    logout.mockResolvedValue(undefined);
    render(<AppSidebar />);

    fireEvent.click(signOutButton() as HTMLElement);

    await waitFor(() => expect(replace).toHaveBeenCalledExactlyOnceWith("/login"));
    expect(logout).toHaveBeenCalledOnce();
    expect(setSignedIn).toHaveBeenCalledExactlyOnceWith(false);
  });

  it("says so and stays when the request does not get through", async () => {
    sessionUser.mockReturnValue(ME);
    logout.mockRejectedValue(new Error("network"));
    render(<AppSidebar />);

    fireEvent.click(signOutButton() as HTMLElement);

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("로그아웃하지 못했습니다"),
    );
    expect(replace).not.toHaveBeenCalled();
    expect(setSignedIn).not.toHaveBeenCalled();
  });
});
