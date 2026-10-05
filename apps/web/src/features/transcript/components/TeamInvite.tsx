"use client";

import { useEffect, useState, type FormEvent } from "react";

import { disconnectGmail, getGmailConnection, googleGmailConnectUrl } from "@/shared/api/auth";
import { Button } from "@/shared/ui";

import { inviteToTeam } from "../api";
import { invitationLink } from "../invitationLink";

/**
 * Invite somebody to a team by a link they open themselves (#552).
 *
 * **An invitation is not a membership.** Making one adds nobody to the team:
 * the person named joins only when they open the link signed in under that
 * address and accept. That is why this asks for an address and hands back a
 * link, and never shows whether the address has an account -- the server does
 * not look.
 *
 * **The link is shown once.** Only a hash of its token is stored, so it cannot
 * be shown again; a second invitation to the same address makes a new link
 * and the earlier one stops working. The list below is what was made on this
 * visit and is gone on reload.
 *
 * **Mail goes from the inviter's own Gmail, when they ask.** Someone who has
 * connected Gmail (`gmail.send` only) can have the link mailed from their own
 * address; the link is still shown and can still be copied, because a mail
 * that did not go is only a mail that did not go. Someone who has not sees how
 * to connect -- where `canConnectMail` says the screen survives the round trip
 * to Google, which the workspace step does not.
 */

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const INPUT =
  "w-full rounded-[var(--radius)] bg-[var(--color-surface-panel)] px-3 text-[var(--color-ink-strong)] focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)]";
const INPUT_STYLE = {
  height: "var(--control-h-default)",
  fontSize: "var(--text-rowBody)",
  border: "1px solid var(--color-hairline)",
} as const;
const META = { fontSize: "var(--text-meta)" } as const;

type Made = { email: string; link: string; expiresAt: string; mail: "sent" | "failed" | null };

type Gmail = { connected: boolean; needs_reconnect?: boolean };

export function TeamInvite({
  teamId,
  canConnectMail = false,
}: {
  teamId: string;
  canConnectMail?: boolean;
}) {
  const [gmail, setGmail] = useState<Gmail | null>(null);
  // Off until the inviter ticks it, each time: the API's own default, and
  // privacy.md's "when they ask" -- an address and a link go to Google only by
  // a choice made for this invitation (mkkim68, review of #760).
  const [sendMail, setSendMail] = useState(false);
  const [mailNote, setMailNote] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [made, setMade] = useState<Made[]>([]);
  const [copied, setCopied] = useState<{ email: string; ok: boolean } | null>(null);

  useEffect(() => {
    let alive = true;
    void getGmailConnection().then((status) => {
      if (!alive || status === null) return;
      setGmail(status);
      const url = new URL(window.location.href);
      const result = url.searchParams.get("gmail");
      if (result === "connected" && status.connected) {
        setMailNote("Gmail을 연결했습니다. 이제 초대 메일을 내 주소로 보낼 수 있습니다.");
      } else if (result === "failed") {
        setMailNote(
          "Gmail을 연결하지 못했습니다. Google 화면에서 메일 보내기 권한에 체크한 채로 다시 시도해 주세요.",
        );
      } else if (status.connected && status.needs_reconnect) {
        setMailNote("Gmail 연결이 끊겼습니다. 다시 연결해 주세요.");
      }
      if (result !== null) {
        url.searchParams.delete("gmail");
        window.history.replaceState(null, "", url.toString());
      }
    });
    return () => {
      alive = false;
    };
  }, []);

  const address = email.trim();
  const canSubmit = EMAIL.test(address) && !pending;
  const canMail = gmail !== null && gmail.connected && !gmail.needs_reconnect;
  const mailing = canMail && sendMail;

  const connectGmail = () => {
    const here = window.location.pathname + window.location.search;
    window.location.assign(googleGmailConnectUrl(here));
  };

  // Withdrawing what was granted is as close as granting it (mkkim68, review of #760).
  const disconnect = async () => {
    setDisconnecting(true);
    try {
      const { revoked } = await disconnectGmail();
      setGmail({ connected: false });
      setMailNote(
        revoked
          ? "Gmail 연결을 해제했습니다. 같은 Google 계정의 캘린더 연결도 다시 해야 할 수 있습니다."
          : "연결을 해제했습니다. Google 계정 설정에서 Autune 접근도 확인해 주세요.",
      );
    } catch {
      setMailNote("연결을 해제하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setDisconnecting(false);
    }
  };

  const disconnectButton =
    gmail?.connected && canConnectMail ? (
      <Button
        tone="quiet"
        size="compact"
        type="button"
        loading={disconnecting}
        onClick={() => void disconnect()}
      >
        Gmail 연결 해제
      </Button>
    ) : null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setPending(true);
    setError(null);
    try {
      const issued = mailing
        ? await inviteToTeam(teamId, address, true)
        : await inviteToTeam(teamId, address);
      const entry: Made = {
        email: address.toLowerCase(),
        link: invitationLink(window.location.origin, issued.token),
        expiresAt: issued.expires_at,
        mail: mailing ? (issued.emailed ? "sent" : "failed") : null,
      };
      // A new link for an address replaces its old one, here as on the server.
      setMade((before) => [entry, ...before.filter((m) => m.email !== entry.email)]);
      setCopied(null);
      setEmail("");
    } catch {
      setError("초대 링크를 만들지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setPending(false);
    }
  };

  const copy = async (entry: Made) => {
    try {
      await navigator.clipboard.writeText(entry.link);
      setCopied({ email: entry.email, ok: true });
    } catch {
      setCopied({ email: entry.email, ok: false });
    }
  };

  return (
    <section aria-label="팀원 초대" className="flex flex-col gap-3">
      <p className="text-[var(--color-ink-muted)]" style={META}>
        {canMail
          ? "초대할 사람의 이메일 주소로 링크를 만들어 내 Gmail로 보내거나 직접 전달해 주세요."
          : "초대할 사람의 이메일 주소로 링크를 만들어 직접 전달해 주세요."}{" "}
        그 주소로 로그인한 사람만 수락할 수 있고, 수락하기 전에는 팀원이 되지 않습니다.
      </p>

      {canMail ? (
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-[var(--color-ink-body)]" style={META}>
            <input
              type="checkbox"
              checked={sendMail}
              onChange={(event) => setSendMail(event.target.checked)}
            />
            내 Gmail로 초대 메일 보내기
          </label>
          {disconnectButton}
        </div>
      ) : gmail !== null && canConnectMail ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button tone="text" size="compact" type="button" onClick={connectGmail}>
            {gmail.needs_reconnect ? "Gmail 다시 연결" : "Gmail 연결하고 초대 메일 보내기"}
          </Button>
          <span className="text-[var(--color-ink-muted)]" style={META}>
            메일 보내기 권한만 받고, 메일함은 읽지 않습니다.
          </span>
          {disconnectButton}
        </div>
      ) : null}

      {mailNote !== null ? (
        <p role="status" className="text-[var(--color-ink-muted)]" style={META}>
          {mailNote}
        </p>
      ) : null}

      <form className="flex flex-wrap items-center gap-2" onSubmit={submit}>
        <input
          type="email"
          aria-label="초대할 이메일 주소"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="name@example.com"
          maxLength={320}
          className={`${INPUT} min-w-0 flex-1`}
          style={INPUT_STYLE}
        />
        <Button tone="secondary" type="submit" disabled={!canSubmit} loading={pending}>
          {mailing ? "초대 메일 보내기" : "초대 링크 만들기"}
        </Button>
      </form>

      {error !== null ? (
        <p role="alert" className="text-[var(--color-signal-critical)]" style={META}>
          {error}
        </p>
      ) : null}

      {made.length > 0 ? (
        <ul className="flex flex-col gap-3">
          {made.map((entry) => (
            <li key={entry.email} className="flex flex-col gap-1">
              <span className="text-[var(--color-ink-body)]" style={META}>
                {entry.email} · {entry.expiresAt.slice(0, 10)}까지
              </span>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  readOnly
                  aria-label={`${entry.email} 초대 링크`}
                  value={entry.link}
                  onFocus={(event) => event.target.select()}
                  className={`${INPUT} min-w-0 flex-1 font-mono`}
                  style={{ ...INPUT_STYLE, fontSize: "var(--text-meta)" }}
                />
                <Button tone="text" size="compact" type="button" onClick={() => void copy(entry)}>
                  복사
                </Button>
              </div>
              {entry.mail !== null ? (
                <span role="status" className="text-[var(--color-ink-muted)]" style={META}>
                  {entry.mail === "sent"
                    ? "내 Gmail로 초대 메일을 보냈습니다."
                    : "메일을 보내지 못했습니다. 링크를 복사해 직접 전달해 주세요."}
                </span>
              ) : null}
              {copied?.email === entry.email ? (
                <span role="status" className="text-[var(--color-ink-muted)]" style={META}>
                  {copied.ok
                    ? "복사했습니다."
                    : "복사하지 못했습니다. 링크를 직접 선택해 복사해 주세요."}
                </span>
              ) : null}
            </li>
          ))}
          <li className="text-[var(--color-ink-muted)]" style={META}>
            링크는 지금만 볼 수 있습니다. 같은 주소로 다시 만들면 이전 링크는 쓸 수 없게 됩니다.
          </li>
        </ul>
      ) : null}
    </section>
  );
}
