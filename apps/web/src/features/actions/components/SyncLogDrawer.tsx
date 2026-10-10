"use client";

import type { Route } from "next";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Button, MaskedText } from "@/shared/ui";

import { getSyncLog } from "../api";
import type { SyncLog, SyncLogCopy } from "../types";
import { SYSTEM_LABEL, whatFailed } from "./SyncStatus";

/**
 * S28's "동기화 기록": what the team's action items did on their way to
 * Notion, Jira and a calendar lately -- the copies that failed and still
 * stand, and the latest that were made.
 *
 * **It reads what the board already knows; it is not a log of every
 * attempt.** A failure leaves the list once a later attempt goes through, and
 * a copy's time is when it was first made. The window says so, so nobody
 * reads an empty list as "nothing was ever sent".
 *
 * **A calendar row is the reader's own.** The server sends a failed calendar
 * copy to the item's assignee and an event to the person whose calendar holds
 * it, and this draws only what it was sent -- hence "내 캘린더".
 *
 * Nothing is retried from here. A row leads to the meeting's 할 일 tab, where
 * the card has "다시 시도" and the item can be corrected first.
 */
export function SyncLogDrawer({ teamId, onClose }: { teamId: string; onClose: () => void }) {
  const [log, setLog] = useState<SyncLog | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    getSyncLog(teamId)
      .then((read) => {
        if (alive) setLog(read);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [teamId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal
      aria-label="동기화 기록"
      className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ background: "rgba(22,25,31,.35)", padding: "var(--space-page)" }}
      onClick={onClose}
    >
      <aside
        className="flex w-full max-w-[560px] flex-col overflow-y-auto"
        style={{
          background: "var(--color-surface-panel)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--shadow-overlay)",
          maxHeight: "calc(100vh - 2 * var(--space-page))",
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <header
          className="flex items-start gap-3 border-b border-[var(--color-hairline)]"
          style={{ padding: "var(--space-card)" }}
        >
          <div className="min-w-0 flex-1">
            <h2 className="text-[var(--color-ink-strong)]" style={HEADING}>
              동기화 기록
            </h2>
            <p className="mt-1 text-[var(--color-ink-muted)]" style={META}>
              이 팀의 할 일을 Notion, Jira, 캘린더로 보낸 기록입니다. 캘린더는 내
              캘린더의 것만 보이고, 각각 최근 30건까지 보입니다.
            </p>
          </div>
          <Button tone="quiet" size="compact" onClick={onClose} aria-label="닫기">
            닫기
          </Button>
        </header>

        <div className="grid gap-6" style={{ padding: "var(--space-card)" }}>
          {failed ? (
            <p role="alert" className="text-[var(--color-ink-muted)]" style={META}>
              동기화 기록을 불러오지 못했습니다. 잠시 후 다시 열어 주세요.
            </p>
          ) : log === null ? (
            <p className="text-[var(--color-ink-muted)]" style={META}>
              불러오는 중입니다.
            </p>
          ) : (
            <>
              <section aria-label="보내지 못한 것" className="grid gap-2">
                <h3 className="text-[var(--color-ink-strong)]" style={HEADING}>
                  보내지 못한 것
                </h3>
                {log.failures.length === 0 ? (
                  <p className="text-[var(--color-ink-muted)]" style={META}>
                    지금 보내지 못한 채 남아 있는 것이 없습니다.
                  </p>
                ) : (
                  <>
                    <p className="text-[var(--color-ink-muted)]" style={META}>
                      다시 보내 성공하면 여기서 사라집니다. 다시 시도는 그 회의의 할 일
                      탭에서 할 수 있습니다.
                    </p>
                    <ul className="grid gap-2">
                      {log.failures.map((failure) => (
                        <li
                          key={`${failure.action_item_id}-${failure.system}`}
                          className="grid gap-1 border-b border-[var(--color-hairline)] pb-2"
                        >
                          <ItemLine row={failure} />
                          <span className="text-[var(--color-signal-critical)]" style={META}>
                            {SYSTEM_LABEL[failure.system]} · {whatFailed(failure)}
                          </span>
                          <span className="text-[var(--color-ink-muted)]" style={META}>
                            {failure.meeting_title} · {when(failure.failed_at)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </section>

              <section aria-label="최근에 보낸 것" className="grid gap-2">
                <h3 className="text-[var(--color-ink-strong)]" style={HEADING}>
                  최근에 보낸 것
                </h3>
                {log.copies.length === 0 ? (
                  <p className="text-[var(--color-ink-muted)]" style={META}>
                    아직 보낸 것이 없습니다.
                  </p>
                ) : (
                  <>
                    <p className="text-[var(--color-ink-muted)]" style={META}>
                      시각은 처음 보낸 때입니다. 그 뒤의 수정은 같은 페이지, 이슈, 일정에
                      반영됩니다.
                    </p>
                    <ul className="grid gap-2">
                      {log.copies.map((copy) => (
                        <li
                          key={`${copy.action_item_id}-${copy.system}`}
                          className="grid gap-1 border-b border-[var(--color-hairline)] pb-2"
                        >
                          <ItemLine row={copy} />
                          <span className="text-[var(--color-ink-muted)]" style={META}>
                            <Where copy={copy} /> · {copy.meeting_title} · {when(copy.copied_at)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </section>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

const META = { fontSize: "var(--text-metaSmall)" } as const;
const HEADING = {
  fontSize: "var(--text-status)",
  fontWeight: "var(--text-status-weight)",
} as const;

/** The item, as a way to its meeting's 할 일 tab -- where its card is. */
function ItemLine({ row }: { row: { meeting_id: string; description: string } }) {
  return (
    <Link
      href={`/meetings/${encodeURIComponent(row.meeting_id)}/actions` as Route}
      className="break-words text-[var(--color-ink-strong)] underline-offset-2 hover:underline"
    >
      <MaskedText>{row.description}</MaskedText>
    </Link>
  );
}

/**
 * Where the copy went, as a link when there is a page or an issue to open.
 * Only an https address becomes one: the address was stored by the server
 * from the tool's answer, and anything else is shown as a name.
 */
function Where({ copy }: { copy: SyncLogCopy }) {
  if (copy.system === "calendar") return <>내 캘린더</>;
  const label = SYSTEM_LABEL[copy.system];
  if (copy.url === null || !copy.url.startsWith("https://")) return <>{label}</>;
  return (
    <a href={copy.url} target="_blank" rel="noreferrer" className="underline underline-offset-2">
      {label}에서 열기
    </a>
  );
}

function when(at: string): string {
  return new Date(at).toLocaleString("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
