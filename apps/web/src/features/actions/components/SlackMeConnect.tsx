"use client";

import { useEffect, useState } from "react";

import { getSlackMe, slackMeConnectUrl, unlinkSlackMe } from "@/shared/api/auth";
import { Button } from "@/shared/ui";

/**
 * One button to link the person's own Slack account (#255), so Autune's direct
 * messages -- a confirmation to answer, feedback that is only theirs -- reach
 * them. Only their Slack member id is kept; no email, no directory (#70).
 */
export function SlackMeConnect() {
  const [linked, setLinked] = useState<boolean | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void getSlackMe().then((status) => {
      if (!alive || status === null) return;
      setLinked(status.linked);
      const url = new URL(window.location.href);
      const result = url.searchParams.get("slack_me");
      if (result === "connected") setNote("내 Slack 계정을 연결했습니다. 개인 알림은 DM으로 옵니다.");
      else if (result === "failed") setNote("Slack 계정을 연결하지 못했습니다. 다시 시도해 주세요.");
      if (result !== null) {
        url.searchParams.delete("slack_me");
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
            내 Slack 계정 연결됨 (DM 받음)
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
          내 Slack 계정 연결 (DM 받기)
        </Button>
      )}
      {note ? (
        <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>
          {note}
        </span>
      ) : null}
    </div>
  );
}
