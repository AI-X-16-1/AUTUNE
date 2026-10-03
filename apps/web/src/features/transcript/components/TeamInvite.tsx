"use client";

import { useState, type FormEvent } from "react";

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
 * Sending the link is the inviter's to do -- mail and Slack delivery are not
 * built -- hence the copy button.
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

type Made = { email: string; link: string; expiresAt: string };

export function TeamInvite({ teamId }: { teamId: string }) {
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [made, setMade] = useState<Made[]>([]);
  const [copied, setCopied] = useState<{ email: string; ok: boolean } | null>(null);

  const address = email.trim();
  const canSubmit = EMAIL.test(address) && !pending;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setPending(true);
    setError(null);
    try {
      const issued = await inviteToTeam(teamId, address);
      const entry: Made = {
        email: address.toLowerCase(),
        link: invitationLink(window.location.origin, issued.token),
        expiresAt: issued.expires_at,
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
        초대할 사람의 이메일 주소로 링크를 만들어 직접 전달해 주세요. 그 주소로 로그인한 사람만
        수락할 수 있고, 수락하기 전에는 팀원이 되지 않습니다.
      </p>

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
          초대 링크 만들기
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
