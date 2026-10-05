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
 * address and accept. That is why this asks for an address and never shows
 * whether the address has an account -- the server does not look.
 *
 * **The button sends the mail** (the user, 2026-10-05). Someone who has
 * connected Gmail (`gmail.send` only) presses "메일 전송" and the invitation
 * is made and mailed from their own address in one step. Nothing is ticked
 * beforehand and nothing is remembered: an address and a link go to Google
 * only by that press, once for each invitation (mkkim68, review of #760).
 * Someone who has not connected sees how to -- where `canConnectMail` says
 * the screen survives the round trip to Google, which the workspace step
 * does not.
 *
 * **Copying the link is the other way, and it stays.** "초대 링크 복사" makes
 * the invitation without mailing anybody and puts the link on the clipboard:
 * the only way for someone without Gmail, and the way out when a mail did not
 * go. The link itself is put on the screen only when the browser would not
 * copy it.
 *
 * **The link can be had on this visit only.** Only a hash of its token is
 * stored, so it cannot be given again; inviting the same address again --
 * "다시 보내기" does exactly that -- makes a new link and the earlier one
 * stops working. The list below is what was made on this visit and is gone on
 * reload.
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

type How = "mail" | "copy";

type Made = {
  email: string;
  link: string;
  expiresAt: string;
  mail: "sent" | "failed" | null;
  /** The browser would not copy it, so it is there to select by hand. */
  showLink: boolean;
};

type Gmail = { connected: boolean; needs_reconnect?: boolean };

async function toClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function TeamInvite({
  teamId,
  canConnectMail = false,
}: {
  teamId: string;
  canConnectMail?: boolean;
}) {
  const [gmail, setGmail] = useState<Gmail | null>(null);
  const [mailNote, setMailNote] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState<How | null>(null);
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
  const canSubmit = EMAIL.test(address) && pending === null;
  const canMail = gmail !== null && gmail.connected && !gmail.needs_reconnect;
  const canOfferMail = !canMail && gmail !== null && canConnectMail;

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

  /** Make an invitation for `to` and mail it or copy its link. Whether it was made. */
  const invite = async (to: string, how: How): Promise<boolean> => {
    setPending(how);
    setError(null);
    try {
      const issued =
        how === "mail" ? await inviteToTeam(teamId, to, true) : await inviteToTeam(teamId, to);
      const link = invitationLink(window.location.origin, issued.token);
      const wrote = how === "copy" ? await toClipboard(link) : null;
      const entry: Made = {
        email: to.toLowerCase(),
        link,
        expiresAt: issued.expires_at,
        mail: how === "mail" ? (issued.emailed ? "sent" : "failed") : null,
        showLink: wrote === false,
      };
      // A new link for an address replaces its old one, here as on the server.
      setMade((before) => [entry, ...before.filter((m) => m.email !== entry.email)]);
      setCopied(wrote === null ? null : { email: entry.email, ok: wrote });
      return true;
    } catch {
      setError("초대하지 못했습니다. 잠시 후 다시 시도해 주세요.");
      return false;
    } finally {
      setPending(null);
    }
  };

  const start = async (how: How) => {
    if (!canSubmit) return;
    if (await invite(address, how)) setEmail("");
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void start(canMail ? "mail" : "copy");
  };

  const copy = async (entry: Made) => {
    const ok = await toClipboard(entry.link);
    if (!ok) {
      setMade((before) => before.map((m) => (m.email === entry.email ? { ...m, showLink: true } : m)));
    }
    setCopied({ email: entry.email, ok });
  };

  return (
    <section aria-label="팀원 초대" className="flex flex-col gap-3">
      <p className="text-[var(--color-ink-muted)]" style={META}>
        {canMail
          ? "초대할 사람의 이메일 주소를 넣고 메일 전송을 누르면 내 Gmail로 초대 메일이 갑니다."
          : "초대할 사람의 이메일 주소로 초대 링크를 복사해 직접 전달해 주세요."}{" "}
        그 주소로 로그인한 사람만 수락할 수 있고, 수락하기 전에는 팀원이 되지 않습니다.
      </p>

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
        {canMail ? (
          <>
            <Button
              tone="secondary"
              type="submit"
              disabled={!canSubmit}
              loading={pending === "mail"}
            >
              메일 전송
            </Button>
            <Button
              tone="text"
              size="compact"
              type="button"
              disabled={!canSubmit}
              loading={pending === "copy"}
              onClick={() => void start("copy")}
            >
              초대 링크 복사
            </Button>
          </>
        ) : (
          <Button
            tone="secondary"
            type="submit"
            disabled={!canSubmit}
            loading={pending === "copy"}
          >
            초대 링크 복사
          </Button>
        )}
      </form>

      {canOfferMail ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button tone="text" size="compact" type="button" onClick={connectGmail}>
            {gmail.needs_reconnect ? "Gmail 다시 연결" : "Gmail 연결하고 메일로 보내기"}
          </Button>
          <span className="text-[var(--color-ink-muted)]" style={META}>
            메일 보내기 권한만 받고, 메일함은 읽지 않습니다.
          </span>
        </div>
      ) : null}

      {gmail?.connected && canConnectMail ? (
        <div>
          <Button
            tone="quiet"
            size="compact"
            type="button"
            loading={disconnecting}
            onClick={() => void disconnect()}
          >
            Gmail 연결 해제
          </Button>
        </div>
      ) : null}

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
              {entry.mail !== null ? (
                <span role="status" className="text-[var(--color-ink-muted)]" style={META}>
                  {entry.mail === "sent"
                    ? "내 Gmail로 초대 메일을 보냈습니다."
                    : "메일을 보내지 못했습니다. 링크를 복사해 직접 전달해 주세요."}
                </span>
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                {entry.showLink ? (
                  <input
                    readOnly
                    aria-label={`${entry.email} 초대 링크`}
                    value={entry.link}
                    onFocus={(event) => event.target.select()}
                    className={`${INPUT} min-w-0 flex-1 font-mono`}
                    style={{ ...INPUT_STYLE, fontSize: "var(--text-meta)" }}
                  />
                ) : null}
                <Button
                  tone="text"
                  size="compact"
                  type="button"
                  aria-label={`${entry.email} 링크 복사`}
                  onClick={() => void copy(entry)}
                >
                  링크 복사
                </Button>
                {canMail ? (
                  <Button
                    tone="text"
                    size="compact"
                    type="button"
                    aria-label={`${entry.email} 다시 보내기`}
                    disabled={pending !== null}
                    onClick={() => void invite(entry.email, "mail")}
                  >
                    다시 보내기
                  </Button>
                ) : null}
              </div>
              {copied?.email === entry.email ? (
                <span role="status" className="text-[var(--color-ink-muted)]" style={META}>
                  {copied.ok
                    ? "초대 링크를 복사했습니다. 직접 전달해 주세요."
                    : "복사하지 못했습니다. 링크를 직접 선택해 복사해 주세요."}
                </span>
              ) : null}
            </li>
          ))}
          <li className="text-[var(--color-ink-muted)]" style={META}>
            링크는 지금만 복사할 수 있습니다. 같은 주소를 다시 초대하면 이전 링크는 쓸 수 없게
            됩니다.
          </li>
        </ul>
      ) : null}
    </section>
  );
}
