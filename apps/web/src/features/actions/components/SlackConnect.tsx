"use client";

import { useEffect, useState } from "react";

import {
  disconnectSlack,
  getSlackConnection,
  slackConnectUrl,
  type SlackConnection,
} from "@/shared/api/auth";
import { Button } from "@/shared/ui";

/**
 * One button to install Autune's bot in the team's Slack (#428). The install
 * makes a private #autune (or #autune-2... when taken), invites whoever
 * installed, and the team's briefings and reports go there.
 */
export function SlackConnect({ meetingId }: { meetingId: string }) {
  const [state, setState] = useState<SlackConnection | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    void getSlackConnection(meetingId).then((status) => {
      if (!alive) return;
      setState(status);
      const url = new URL(window.location.href);
      const result = url.searchParams.get("slack");
      if (result === "connected")
        setNote(
          status?.connected && status.channel_name
            ? `Slack에 연결했습니다. 비공개 채널 #${status.channel_name}에 초대했으니 팀원을 추가해 주세요.`
            : "Slack에 연결했습니다.",
        );
      else if (result === "failed")
        setNote(
          url.searchParams.get("reason") === "slack_channel_unavailable"
            ? "알림 채널 이름(#autune ~ #autune-10)이 모두 사용 중입니다. 하나를 비우고 다시 연결해 주세요."
            : "Slack을 연결하지 못했습니다. 다시 시도해 주세요.",
        );
      if (result !== null) {
        url.searchParams.delete("slack");
        url.searchParams.delete("reason");
        window.history.replaceState(null, "", url.toString());
      }
    });
    return () => {
      alive = false;
    };
  }, [meetingId]);

  if (state === null) return null;
  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  const status = note ? (
    <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>
      {note}
    </span>
  ) : null;

  if (!state.connected) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <Button
          tone="text"
          size="compact"
          onClick={() => {
            const here = window.location.pathname + window.location.search;
            window.location.assign(slackConnectUrl(meetingId, here));
          }}
        >
          팀 Slack 연결
        </Button>
        {status}
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-[var(--color-ink-muted)]" style={meta}>
        Slack 연결됨 · {state.workspace_name ?? "워크스페이스"}
        {state.channel_name ? ` · #${state.channel_name} (비공개)` : ""}
      </span>
      <Button
        tone="quiet"
        size="compact"
        loading={busy}
        onClick={() => {
          setBusy(true);
          void disconnectSlack(meetingId)
            .then(({ revoked, shared }) => {
              setState({ connected: false });
              setNote(
                shared
                  ? "연결을 해제했습니다. 같은 워크스페이스의 다른 팀이 Autune을 쓰고 있어 봇은 남겨 두었습니다."
                  : revoked
                    ? "Slack 연결을 해제했습니다."
                    : "연결을 해제했습니다. Slack 앱 관리에서 Autune도 확인해 주세요.",
              );
            })
            .catch(() => setNote("연결을 해제하지 못했습니다."))
            .finally(() => setBusy(false));
        }}
      >
        연결 해제
      </Button>
      {status}
    </div>
  );
}
