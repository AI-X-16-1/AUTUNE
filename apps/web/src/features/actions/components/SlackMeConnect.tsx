"use client";

import { useEffect, useState } from "react";

import {
  getSlackMe,
  slackMeConnectUrl,
  unlinkSlackMe,
} from "@/shared/api/auth";
import { Button } from "@/shared/ui";

/**
 * One button to link the person's own Slack account (#255), so Autune's direct
 * messages -- a confirmation to answer, feedback that is only theirs -- reach
 * them. Only their Slack member id is kept; no email, no directory (#70).
 */
const RETRY = "Slack 계정을 연결하지 못했습니다. 다시 시도해 주세요.";

/** Why a link was refused, by the server's code -- each one says what to do. */
const REFUSED: Record<string, string> = {
  slack_team_not_connected:
    "팀이 아직 Slack을 연결하지 않았습니다. 먼저 '팀 Slack 연결'을 해 주세요.",
  slack_wrong_workspace:
    "팀이 Autune을 설치한 Slack 워크스페이스가 아닙니다. 브라우저에서 그 워크스페이스로 로그인한 뒤 다시 연결해 주세요.",
  slack_account_taken:
    "이 Slack 계정은 다른 사람의 Autune 계정에 연결되어 있습니다. 브라우저에 다른 사람의 Slack 로그인이 남아 있지 않은지 확인해 주세요.",
  slack_link_not_confirmed:
    "확인 링크가 맞지 않거나 만료되었습니다. 연결을 시작한 이 브라우저에서, 30분 안에 받은 링크를 열어 주세요.",
};

/** The link waits until the Slack account itself confirms it (#478). */
const PENDING =
  "Slack DM으로 확인 링크를 보냈습니다. 이 브라우저에서 그 링크를 열면 연결이 끝납니다. DM이 오지 않았다면 브라우저에 다른 사람의 Slack 로그인이 남아 있는지 확인해 주세요.";

export function SlackMeConnect() {
  const [linked, setLinked] = useState<boolean | null>(null);
  const [pending, setPending] = useState(false);
  // Slack's own redirect to the DM the bot just sent -- never the confirmation
  // link itself: this browser may hold someone else's Slack session (#478).
  const [dmUrl, setDmUrl] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void getSlackMe().then((status) => {
      if (!alive || status === null) return;
      setLinked(status.linked);
      setPending(Boolean(status.pending));
      setDmUrl(status.pending ? (status.dm_url ?? null) : null);
      setWorkspace(status.workspace_name ?? null);
      const url = new URL(window.location.href);
      const result = url.searchParams.get("slack_me");
      if (result === "connected")
        setNote("내 Slack 계정을 연결했습니다. 개인 알림은 DM으로 옵니다.");
      else if (result === "pending") setNote(PENDING);
      else if (result === "failed")
        setNote(REFUSED[url.searchParams.get("reason") ?? ""] ?? RETRY);
      if (result !== null) {
        url.searchParams.delete("slack_me");
        url.searchParams.delete("reason");
        window.history.replaceState(null, "", url.toString());
      }
    });
    return () => {
      alive = false;
    };
  }, []);

  if (linked === null) return null;
  const meta = { fontSize: "var(--text-metaSmall)" } as const;

  return (
    <div className="flex flex-wrap items-center gap-3">
      {linked ? (
        <>
          <span className="text-[var(--color-ink-muted)]" style={meta}>
            내 Slack 계정 연결됨{workspace ? ` · ${workspace}` : ""} (DM 받음)
          </span>
          <Button
            tone="quiet"
            size="compact"
            onClick={() =>
              void unlinkSlackMe()
                .then(() => {
                  setLinked(false);
                  setNote("Slack 계정 연결을 해제했습니다.");
                })
                .catch(() => setNote("연결을 해제하지 못했습니다."))
            }
          >
            연결 해제
          </Button>
        </>
      ) : (
        <Button
          tone="text"
          size="compact"
          onClick={() => {
            const here = window.location.pathname + window.location.search;
            window.location.assign(slackMeConnectUrl(here));
          }}
        >
          {pending
            ? "Slack 확인 링크 다시 받기"
            : "내 Slack 계정 연결 (DM 받기)"}
        </Button>
      )}
      {note ? (
        <span
          role="status"
          className="text-[var(--color-ink-muted)]"
          style={meta}
        >
          {note}
        </span>
      ) : null}
      {pending && dmUrl ? (
        <a
          href={dmUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[var(--color-accent-default)] underline"
          style={meta}
        >
          Slack에서 확인 DM 열기
        </a>
      ) : null}
    </div>
  );
}
