"use client";

import { useEffect, useState } from "react";

import {
  getNotificationPause,
  setNotificationPause,
  type NotificationPause,
  type NotificationPauseRead,
} from "../api";

/**
 * The person's own leave dates: no morning DM and no Monday digest from the
 * first day to the last, both included. Due-date reminders still go. It
 * changes only their own; nobody else's dates are shown here or anywhere.
 */
export function NotificationPauseSetting() {
  const [saved, setSaved] = useState<NotificationPauseRead | null>(null);
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  const show = (answer: NotificationPauseRead) => {
    setSaved(answer);
    setFirst(answer.starts_on ?? "");
    setLast(answer.ends_on ?? "");
  };

  useEffect(() => {
    let alive = true;
    getNotificationPause()
      .then((answer) => {
        if (alive) show(answer);
      })
      .catch(() => {
        // Not shown at all rather than shown wrong: the rest of the page works.
      });
    return () => {
      alive = false;
    };
  }, []);

  if (saved === null) return null;
  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  const backwards = first !== "" && last !== "" && last < first;
  const ready = first !== "" && last !== "" && !backwards;

  const send = (pause: NotificationPause) => {
    setSaving(true);
    setFailed(false);
    setNotificationPause(pause)
      .then(show)
      .catch(() => setFailed(true))
      .finally(() => setSaving(false));
  };

  return (
    <div className="flex flex-col gap-1">
      <div
        className="flex flex-wrap items-center gap-2 text-[var(--color-ink-body)]"
        style={meta}
      >
        <span>
          휴가 기간 (아침 요약과 월요일 요약을 받지 않음. 월요일이 공휴일이라 다른 날
          오는 월요일 요약도 같습니다)
        </span>
        <input
          type="date"
          aria-label="휴가 시작일"
          value={first}
          disabled={saving}
          onChange={(event) => setFirst(event.target.value)}
        />
        <span aria-hidden="true">~</span>
        <input
          type="date"
          aria-label="휴가 종료일"
          value={last}
          min={first || undefined}
          disabled={saving}
          onChange={(event) => setLast(event.target.value)}
        />
        <button
          type="button"
          disabled={saving || !ready}
          onClick={() => send({ starts_on: first, ends_on: last })}
        >
          저장
        </button>
        {saved.starts_on !== null && (
          <button
            type="button"
            disabled={saving}
            onClick={() => send({ starts_on: null, ends_on: null })}
          >
            해제
          </button>
        )}
      </div>
      <span className="text-[var(--color-ink-muted)]" style={meta}>
        {saved.starts_on !== null && saved.ends_on !== null
          ? `${saved.starts_on}부터 ${saved.ends_on}까지 보내지 않습니다. 마감 알림은 그대로 갑니다.`
          : "기간을 정하면 그동안 보내지 않습니다. 마감 알림은 그대로 갑니다."}
      </span>
      <span className="text-[var(--color-ink-muted)]" style={meta}>
        공휴일에는 보내지 않습니다.
        {saved.calendar_leave
          ? " 연결한 Google 캘린더에 '부재중' 일정이 있는 시간에도 보내지 않습니다. 부재중 일정의 시간만 읽고, 다른 일정은 읽지 않습니다."
          : ""}
      </span>
      {backwards && (
        <span
          role="alert"
          className="text-[var(--color-signal-critical)]"
          style={meta}
        >
          종료일이 시작일보다 빠릅니다.
        </span>
      )}
      {failed && (
        <span
          role="alert"
          className="text-[var(--color-signal-critical)]"
          style={meta}
        >
          기간을 저장하지 못했습니다. 다시 시도해 주세요.
        </span>
      )}
    </div>
  );
}
