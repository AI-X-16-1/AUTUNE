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

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
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

const ME = { id: "user_me", email: "me@example.com", display_name: "Me", teams: [] };

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const signOutButton = () => screen.queryByRole("button", { name: "로그아웃" });

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
