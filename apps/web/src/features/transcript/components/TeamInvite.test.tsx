import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TeamInvite } from "./TeamInvite";

// Inviting by a link the invited person opens themselves (#552). The screen
// asks for an address, hands back a link once, and never says anything about
// the address -- the server does not look.

const invite = vi.fn<(teamId: string, email: string) => Promise<{ token: string; expires_at: string }>>();
vi.mock("../api", () => ({
  inviteToTeam: (teamId: string, email: string) => invite(teamId, email),
}));

const ISSUED = { token: "tok_abc", expires_at: "2026-10-09T10:00:00Z" };

function make(email: string) {
  fireEvent.change(screen.getByLabelText("초대할 이메일 주소"), { target: { value: email } });
  fireEvent.click(screen.getByRole("button", { name: "초대 링크 만들기" }));
}

afterEach(() => {
  cleanup();
  invite.mockReset();
  vi.unstubAllGlobals();
});

describe("TeamInvite", () => {
  it("makes a link that carries the token in the fragment, never in the query", async () => {
    invite.mockResolvedValue(ISSUED);
    render(<TeamInvite teamId="team_1" />);

    make(" Newcomer@Example.com ");

    const link = (await screen.findByLabelText("newcomer@example.com 초대 링크")) as HTMLInputElement;
    expect(invite).toHaveBeenCalledExactlyOnceWith("team_1", "Newcomer@Example.com");
    expect(link.value).toBe(`${window.location.origin}/invite#tok_abc`);
    expect(link.value).not.toContain("?");
    expect(screen.getByText("newcomer@example.com · 2026-10-09까지")).toBeTruthy();
    expect(screen.getByText(/지금만 볼 수 있습니다/)).toBeTruthy();
  });

  it("does not ask the server about something that is not an address", () => {
    render(<TeamInvite teamId="team_1" />);

    fireEvent.change(screen.getByLabelText("초대할 이메일 주소"), { target: { value: "no-at-sign" } });

    expect((screen.getByRole("button", { name: "초대 링크 만들기" }) as HTMLButtonElement).disabled).toBe(true);
    expect(invite).not.toHaveBeenCalled();
  });

  it("copies the link and says so", async () => {
    invite.mockResolvedValue(ISSUED);
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    render(<TeamInvite teamId="team_1" />);
    make("a@example.com");
    await screen.findByLabelText("a@example.com 초대 링크");

    fireEvent.click(screen.getByRole("button", { name: "복사" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("복사했습니다."));
    expect(writeText).toHaveBeenCalledExactlyOnceWith(`${window.location.origin}/invite#tok_abc`);
  });

  it("says so when the browser will not copy, and leaves the link to select", async () => {
    invite.mockResolvedValue(ISSUED);
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: { writeText: () => Promise.reject(new Error("denied")) },
    });
    render(<TeamInvite teamId="team_1" />);
    make("a@example.com");
    await screen.findByLabelText("a@example.com 초대 링크");

    fireEvent.click(screen.getByRole("button", { name: "복사" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("복사하지 못했습니다"));
  });

  it("replaces the link of an address invited again, as the server does", async () => {
    invite.mockResolvedValueOnce(ISSUED);
    invite.mockResolvedValueOnce({ token: "tok_new", expires_at: "2026-10-09T11:00:00Z" });
    render(<TeamInvite teamId="team_1" />);
    make("a@example.com");
    await screen.findByLabelText("a@example.com 초대 링크");

    make("A@example.com");

    await waitFor(() =>
      expect((screen.getByLabelText("a@example.com 초대 링크") as HTMLInputElement).value).toContain(
        "#tok_new",
      ),
    );
    expect(screen.getAllByRole("button", { name: "복사" })).toHaveLength(1);
  });

  it("says so in its own words when the invitation could not be made", async () => {
    invite.mockRejectedValue(new Error("you are not a member of this team"));
    render(<TeamInvite teamId="team_1" />);

    make("a@example.com");

    expect((await screen.findByRole("alert")).textContent).toBe(
      "초대 링크를 만들지 못했습니다. 잠시 후 다시 시도해 주세요.",
    );
    // What was typed stays, to try again.
    expect((screen.getByLabelText("초대할 이메일 주소") as HTMLInputElement).value).toBe("a@example.com");
  });
});
