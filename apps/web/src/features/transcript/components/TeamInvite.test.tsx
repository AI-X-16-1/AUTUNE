import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TeamInvite } from "./TeamInvite";

// Inviting by a link the invited person opens themselves (#552). The screen
// asks for an address and never says anything about the address -- the server
// does not look. With the inviter's own Gmail a button mails the invitation;
// copying the link is the other way and the only one without Gmail.

const invite =
  vi.fn<
    (teamId: string, email: string, sendEmail?: boolean) => Promise<{
      token: string;
      expires_at: string;
      emailed?: boolean;
    }>
  >();
vi.mock("../api", () => ({
  inviteToTeam: (...args: [string, string, boolean?]) => invite(...args),
}));

// The person's own Gmail grant (#552). Unknown by default -- as for a visitor
// whose status call failed -- so nothing about mail shows.
const gmail = vi.fn<() => Promise<{ connected: boolean; needs_reconnect?: boolean } | null>>(() =>
  Promise.resolve(null),
);
const assign = vi.fn();
const disconnect = vi.fn<() => Promise<{ revoked: boolean }>>();
vi.mock("@/shared/api/auth", () => ({
  getGmailConnection: () => gmail(),
  disconnectGmail: () => disconnect(),
  googleGmailConnectUrl: (to: string) => `/api/auth/google/gmail/start?redirect_to=${to}`,
}));

const ISSUED = { token: "tok_abc", expires_at: "2026-10-09T10:00:00Z" };
const LINK = `${window.location.origin}/invite#tok_abc`;

function clipboard(writeText: (text: string) => Promise<void>) {
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
}

function type(email: string) {
  fireEvent.change(screen.getByLabelText("초대할 이메일 주소"), { target: { value: email } });
}

function copyLinkFor(email: string) {
  type(email);
  fireEvent.click(screen.getByRole("button", { name: "초대 링크 복사" }));
}

afterEach(() => {
  cleanup();
  invite.mockReset();
  gmail.mockReset();
  gmail.mockImplementation(() => Promise.resolve(null));
  assign.mockReset();
  disconnect.mockReset();
  vi.unstubAllGlobals();
});

describe("TeamInvite", () => {
  it("makes an invitation and copies a link that carries the token in the fragment", async () => {
    invite.mockResolvedValue(ISSUED);
    const writeText = vi.fn(() => Promise.resolve());
    clipboard(writeText);
    render(<TeamInvite teamId="team_1" />);

    copyLinkFor(" Newcomer@Example.com ");

    await screen.findByText("초대 링크를 복사했습니다. 직접 전달해 주세요.");
    expect(invite).toHaveBeenCalledExactlyOnceWith("team_1", "Newcomer@Example.com");
    expect(writeText).toHaveBeenCalledExactlyOnceWith(LINK);
    expect(LINK).not.toContain("?");
    expect(screen.getByText("newcomer@example.com · 2026-10-09까지")).toBeTruthy();
    expect(screen.getByText(/지금만 복사할 수 있습니다/)).toBeTruthy();
    // Copied, so not also put on the screen.
    expect(screen.queryByLabelText("newcomer@example.com 초대 링크")).toBeNull();
  });

  it("does not ask the server about something that is not an address", () => {
    render(<TeamInvite teamId="team_1" />);

    type("no-at-sign");

    expect(
      (screen.getByRole("button", { name: "초대 링크 복사" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(invite).not.toHaveBeenCalled();
  });

  it("puts the link on the screen to select when the browser will not copy", async () => {
    invite.mockResolvedValue(ISSUED);
    clipboard(() => Promise.reject(new Error("denied")));
    render(<TeamInvite teamId="team_1" />);

    copyLinkFor("a@example.com");

    const link = (await screen.findByLabelText("a@example.com 초대 링크")) as HTMLInputElement;
    expect(link.value).toBe(LINK);
    expect(screen.getByText("복사하지 못했습니다. 링크를 직접 선택해 복사해 주세요.")).toBeTruthy();
  });

  it("copies the same link again from the list without making a new invitation", async () => {
    invite.mockResolvedValue(ISSUED);
    const writeText = vi.fn(() => Promise.resolve());
    clipboard(writeText);
    render(<TeamInvite teamId="team_1" />);
    copyLinkFor("a@example.com");
    await screen.findByText("a@example.com · 2026-10-09까지");

    fireEvent.click(screen.getByRole("button", { name: "a@example.com 링크 복사" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2));
    expect(writeText).toHaveBeenLastCalledWith(LINK);
    expect(invite).toHaveBeenCalledOnce();
  });

  it("replaces the link of an address invited again, as the server does", async () => {
    invite.mockResolvedValueOnce(ISSUED);
    invite.mockResolvedValueOnce({ token: "tok_new", expires_at: "2026-10-09T11:00:00Z" });
    const writeText = vi.fn(() => Promise.resolve());
    clipboard(writeText);
    render(<TeamInvite teamId="team_1" />);
    copyLinkFor("a@example.com");
    await screen.findByText("a@example.com · 2026-10-09까지");

    copyLinkFor("A@example.com");

    await waitFor(() =>
      expect(writeText).toHaveBeenLastCalledWith(`${window.location.origin}/invite#tok_new`),
    );
    expect(screen.getAllByRole("button", { name: "a@example.com 링크 복사" })).toHaveLength(1);
  });

  it("says so in its own words when the invitation could not be made", async () => {
    invite.mockRejectedValue(new Error("you are not a member of this team"));
    render(<TeamInvite teamId="team_1" />);

    copyLinkFor("a@example.com");

    expect((await screen.findByRole("alert")).textContent).toBe(
      "초대하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    );
    // What was typed stays, to try again.
    expect((screen.getByLabelText("초대할 이메일 주소") as HTMLInputElement).value).toBe(
      "a@example.com",
    );
  });

  it("shows no mail button to someone whose Gmail is not connected", async () => {
    gmail.mockResolvedValue({ connected: false });
    render(<TeamInvite teamId="team_1" canConnectMail />);
    await screen.findByRole("button", { name: "Gmail 연결하고 메일로 보내기" });

    expect(screen.queryByRole("button", { name: "메일 전송" })).toBeNull();
    expect(screen.getByRole("button", { name: "초대 링크 복사" })).toBeTruthy();
  });

  describe("with the inviter's own Gmail", () => {
    it("makes the invitation and mails it in one press, with nothing ticked first", async () => {
      gmail.mockResolvedValue({ connected: true });
      invite.mockResolvedValue({ ...ISSUED, emailed: true });
      render(<TeamInvite teamId="team_1" />);
      const send = await screen.findByRole("button", { name: "메일 전송" });
      expect(screen.queryByRole("checkbox")).toBeNull();

      type("a@example.com");
      fireEvent.click(send);

      await screen.findByText("내 Gmail로 초대 메일을 보냈습니다.");
      expect(invite).toHaveBeenCalledExactlyOnceWith("team_1", "a@example.com", true);
      // Mailed, so the link is not put on the screen; it can still be copied.
      expect(screen.queryByLabelText("a@example.com 초대 링크")).toBeNull();
      expect(screen.getByRole("button", { name: "a@example.com 링크 복사" })).toBeTruthy();
      expect((screen.getByLabelText("초대할 이메일 주소") as HTMLInputElement).value).toBe("");
    });

    it("sends nothing to Google until the button is pressed, and nothing by the copy button", async () => {
      gmail.mockResolvedValue({ connected: true });
      invite.mockResolvedValue(ISSUED);
      clipboard(() => Promise.resolve());
      render(<TeamInvite teamId="team_1" />);
      await screen.findByRole("button", { name: "메일 전송" });

      type("a@example.com");
      expect(invite).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "초대 링크 복사" }));

      await screen.findByText("초대 링크를 복사했습니다. 직접 전달해 주세요.");
      expect(invite).toHaveBeenCalledExactlyOnceWith("team_1", "a@example.com");
      expect(screen.queryByText(/메일을 보냈습니다|메일을 보내지 못했습니다/)).toBeNull();
    });

    it("says the mail did not go and leaves the link to copy", async () => {
      gmail.mockResolvedValue({ connected: true });
      invite.mockResolvedValue({ ...ISSUED, emailed: false });
      const writeText = vi.fn(() => Promise.resolve());
      clipboard(writeText);
      render(<TeamInvite teamId="team_1" />);

      type("a@example.com");
      fireEvent.click(await screen.findByRole("button", { name: "메일 전송" }));

      await screen.findByText("메일을 보내지 못했습니다. 링크를 복사해 직접 전달해 주세요.");
      fireEvent.click(screen.getByRole("button", { name: "a@example.com 링크 복사" }));
      await waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith(LINK));
    });

    it("sends again with a new invitation for the same address", async () => {
      gmail.mockResolvedValue({ connected: true });
      invite.mockResolvedValueOnce({ ...ISSUED, emailed: false });
      invite.mockResolvedValueOnce({
        token: "tok_new",
        expires_at: "2026-10-10T10:00:00Z",
        emailed: true,
      });
      render(<TeamInvite teamId="team_1" />);
      type("a@example.com");
      fireEvent.click(await screen.findByRole("button", { name: "메일 전송" }));
      await screen.findByText("메일을 보내지 못했습니다. 링크를 복사해 직접 전달해 주세요.");

      fireEvent.click(screen.getByRole("button", { name: "a@example.com 다시 보내기" }));

      await screen.findByText("내 Gmail로 초대 메일을 보냈습니다.");
      expect(invite).toHaveBeenLastCalledWith("team_1", "a@example.com", true);
      expect(screen.getByText("a@example.com · 2026-10-10까지")).toBeTruthy();
      expect(screen.getAllByRole("button", { name: "a@example.com 다시 보내기" })).toHaveLength(1);
    });

    it("offers no send-again to someone who cannot mail", async () => {
      invite.mockResolvedValue(ISSUED);
      clipboard(() => Promise.resolve());
      render(<TeamInvite teamId="team_1" />);

      copyLinkFor("a@example.com");

      await screen.findByText("a@example.com · 2026-10-09까지");
      expect(screen.queryByRole("button", { name: "a@example.com 다시 보내기" })).toBeNull();
    });

    it("offers to connect Gmail only where the screen survives the trip to Google", async () => {
      gmail.mockResolvedValue({ connected: false });
      vi.stubGlobal("location", {
        ...window.location,
        assign,
        pathname: "/settings/members",
        search: "",
      });
      const { unmount } = render(<TeamInvite teamId="team_1" />);
      await waitFor(() => expect(gmail).toHaveBeenCalled());
      expect(screen.queryByRole("button", { name: "Gmail 연결하고 메일로 보내기" })).toBeNull();
      unmount();

      render(<TeamInvite teamId="team_1" canConnectMail />);
      fireEvent.click(await screen.findByRole("button", { name: "Gmail 연결하고 메일로 보내기" }));

      expect(assign).toHaveBeenCalledExactlyOnceWith(
        "/api/auth/google/gmail/start?redirect_to=/settings/members",
      );
    });

    it("can withdraw the Gmail grant from where it was given", async () => {
      gmail.mockResolvedValue({ connected: true });
      disconnect.mockResolvedValue({ revoked: true });
      render(<TeamInvite teamId="team_1" canConnectMail />);

      fireEvent.click(await screen.findByRole("button", { name: "Gmail 연결 해제" }));

      await screen.findByText(/Gmail 연결을 해제했습니다/);
      expect(disconnect).toHaveBeenCalledOnce();
      expect(screen.queryByRole("button", { name: "메일 전송" })).toBeNull();
      expect(screen.getByRole("button", { name: "Gmail 연결하고 메일로 보내기" })).toBeTruthy();
    });

    it("offers no disconnect where it offers no connect", async () => {
      gmail.mockResolvedValue({ connected: true });
      render(<TeamInvite teamId="team_1" />);
      await screen.findByRole("button", { name: "메일 전송" });

      expect(screen.queryByRole("button", { name: "Gmail 연결 해제" })).toBeNull();
    });
  });
});
