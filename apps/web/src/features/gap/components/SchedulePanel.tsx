"use client";

import { useEffect, useState } from "react";

import { getCalendarConnection, googleCalendarConnectUrl } from "@/shared/api/auth";
import { Button } from "@/shared/ui";

import type { GapAgendaEvents } from "../types";

/** The person's own calendar, in whichever Google account the browser holds. */
const GOOGLE_CALENDAR = "https://calendar.google.com/calendar/";

/**
 * What "다음 회의 잡기" opens (#824): the person's own Google Calendar events for
 * the next two weeks, to pick the one the meeting's open gaps go onto.
 *
 * Not connected, it offers the connection instead — the same flow the sign-in
 * and actions screens use, back to this page with `?calendar=connected`, where
 * the rail opens this panel again. The events are read for this person and
 * shown to them only; nothing about them is stored.
 *
 * **The connect flow is a full navigation, so it is checked first.** It is
 * authenticated by the session cookie, not by a bearer token, and a browser
 * without that session got the server's raw 403 page. The status read
 * (`getCalendarConnection`) uses the same cookie, so when it cannot answer the
 * panel says why instead of navigating. A connect that fails at Google comes
 * back with `?calendar=failed`, and the rail reopens the panel saying so
 * (`connectFailed`).
 */
export function SchedulePanel({
  load,
  pending,
  onPick,
  connectFailed = false,
}: {
  load: () => Promise<GapAgendaEvents>;
  pending: boolean;
  onPick: (eventId: string) => void;
  /** Back from a connect that did not finish. */
  connectFailed?: boolean;
}) {
  const [events, setEvents] = useState<GapAgendaEvents | null>(null);
  const [failed, setFailed] = useState(false);
  const [chosen, setChosen] = useState("");
  const [connectNote, setConnectNote] = useState<string | null>(
    connectFailed
      ? "Google 캘린더를 연결하지 못했습니다. Google 화면에서 캘린더 권한에 체크한 채로 다시 시도해 주세요."
      : null,
  );
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    let live = true;
    load().then(
      (loaded) => {
        if (live) setEvents(loaded);
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [load]);

  const connect = async () => {
    setChecking(true);
    setConnectNote(null);
    const status = await getCalendarConnection();
    setChecking(false);
    if (status === null) {
      setConnectNote(
        "지금 브라우저에는 로그인 세션이 없어 캘린더를 연결할 수 없습니다. 로그인 화면에서 로그인한 뒤 다시 시도해 주세요.",
      );
      return;
    }
    window.location.href = googleCalendarConnectUrl(
      window.location.pathname + window.location.search,
    );
  };

  let body;
  if (failed || events?.calendar === "failed") {
    body = <Note>Google 캘린더를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.</Note>;
  } else if (!events) {
    body = <Note>내 Google 캘린더를 불러오는 중입니다.</Note>;
  } else if (events.calendar === "not_connected" || events.calendar === "reconnect_required") {
    body = (
      <>
        <Note>
          {events.calendar === "not_connected"
            ? "Google 캘린더가 연결되어 있지 않습니다. 연결하면 다음 회의 일정을 골라 이 회의의 열린 갭을 일정 설명에 넣을 수 있습니다."
            : "Google 캘린더 연결이 끊겼습니다. 다시 연결해 주세요."}
        </Note>
        <div>
          <Button
            tone="primary"
            size="compact"
            disabled={checking}
            aria-busy={checking || undefined}
            onClick={() => void connect()}
          >
            Google 캘린더 연결
          </Button>
        </div>
        {connectNote ? (
          <p
            role="alert"
            className="text-[var(--color-signal-critical)]"
            style={{ fontSize: "var(--text-metaSmall)" }}
          >
            {connectNote}
          </p>
        ) : null}
      </>
    );
  } else if (events.events.length === 0) {
    body = <Note>앞으로 2주 동안 내 캘린더에 일정이 없습니다. 캘린더에서 다음 회의를 먼저 만들어 주세요.</Note>;
  } else {
    body = (
      <>
        <fieldset className="flex flex-col" style={{ gap: "var(--space-4)" }}>
          <legend className="sr-only">다음 회의 일정</legend>
          {events.events.map((event) => (
            <label
              key={event.id}
              className="flex items-baseline text-[var(--color-ink-strong)]"
              style={{ gap: "var(--space-8)", fontSize: "var(--text-metaSmall)" }}
            >
              <input
                type="radio"
                name="next-meeting"
                value={event.id}
                checked={chosen === event.id}
                onChange={() => setChosen(event.id)}
              />
              <span className="tabular-nums text-[var(--color-ink-muted)]">
                {formatStart(event.start)}
              </span>
              <span className="min-w-0 truncate">{event.summary || "(제목 없음)"}</span>
            </label>
          ))}
        </fieldset>
        <div>
          <Button
            tone="primary"
            size="compact"
            disabled={!chosen || pending}
            aria-busy={pending || undefined}
            onClick={() => onPick(chosen)}
          >
            {pending ? "처리 중" : "이 일정에 갭 넣기"}
          </Button>
        </div>
      </>
    );
  }

  return (
    <div
      role="dialog"
      aria-label="다음 회의 잡기"
      className="mt-3 flex flex-col border border-[var(--color-hairline)]"
      style={{
        gap: "var(--space-8)",
        padding: "var(--space-12)",
        borderRadius: "var(--radius)",
        background: "var(--color-surface-panel)",
      }}
    >
      <div className="flex items-baseline justify-between" style={{ gap: "var(--space-8)" }}>
        <span
          className="text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-metaSmall)", fontWeight: 600 }}
        >
          내 Google 캘린더 · 앞으로 2주
        </span>
        <a
          href={GOOGLE_CALENDAR}
          target="_blank"
          rel="noreferrer"
          className="text-[var(--color-accent-hover)] underline"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          캘린더 열기
        </a>
      </div>
      {body}
    </div>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
      {children}
    </p>
  );
}

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

/** `10/8(수) 14:00`, in the browser's own time zone. */
export function formatStart(iso: string): string {
  const at = new Date(iso);
  const hh = String(at.getHours()).padStart(2, "0");
  const mm = String(at.getMinutes()).padStart(2, "0");
  return `${at.getMonth() + 1}/${at.getDate()}(${WEEKDAYS[at.getDay()]}) ${hh}:${mm}`;
}
