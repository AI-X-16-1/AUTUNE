"use client";

import { useEffect, useState } from "react";

import {
  disconnectCalendar,
  getCalendarConnection,
  googleCalendarConnectUrl,
} from "@/shared/api/auth";
import { Button } from "@/shared/ui";

/**
 * One button to put the person's own due dates on their own Google Calendar
 * (#435). Connected, a date they drag there comes back here within ten minutes.
 *
 * Only their own tasks go on it -- team work stays on this board -- and only
 * the person themselves can see whether they are connected. Auth is not a
 * module, so the calls go through `@/shared/api/auth` rather than
 * `/api/extraction`, the same way the sign-in screen does.
 *
 * Nothing shows until the status is known, and nothing shows for a visitor
 * without a session: a button that could only fail is worse than none.
 */
export function CalendarConnect() {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    void getCalendarConnection().then((status) => {
      if (!alive || status === null) return;
      setConnected(status.connected);
      const url = new URL(window.location.href);
      if (status.connected && url.searchParams.get("calendar") === "connected") {
        setNote("캘린더를 연결했습니다. 내가 담당인 항목의 마감일이 내 캘린더에 들어갑니다.");
        url.searchParams.delete("calendar");
        window.history.replaceState(null, "", url.toString());
      }
    });
    return () => {
      alive = false;
    };
  }, []);

  if (connected === null) return null;

  const connect = () => {
    const here = window.location.pathname + window.location.search;
    window.location.assign(googleCalendarConnectUrl(here));
  };

  const disconnect = async () => {
    setBusy(true);
    try {
      const { revoked } = await disconnectCalendar();
      setConnected(false);
      setNote(
        revoked
          ? "캘린더 연결을 해제했습니다."
          : "연결을 해제했습니다. Google 계정 설정에서 Autune 접근도 확인해 주세요.",
      );
    } catch {
      setNote("연결을 해제하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      {connected ? (
        <>
          <span
            className="text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-metaSmall)" }}
          >
            내 Google 캘린더 연결됨
          </span>
          <Button tone="quiet" size="compact" loading={busy} onClick={disconnect}>
            연결 해제
          </Button>
        </>
      ) : (
        <Button tone="text" size="compact" onClick={connect}>
          내 Google 캘린더에 마감일 넣기
        </Button>
      )}
      {note ? (
        <span
          role="status"
          className="text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {note}
        </span>
      ) : null}
    </div>
  );
}
