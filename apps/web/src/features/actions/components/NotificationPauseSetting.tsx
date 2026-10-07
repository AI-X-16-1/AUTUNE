"use client";

import { useEffect, useState } from "react";

import {
  getNotificationPause,
  setNotificationPause,
  type LeaveCalendarOutcome,
  type NotificationPause,
  type NotificationPauseRead,
} from "../api";

/**
 * The person's own leave dates: no morning DM and no Monday digest from the
 * first day to the last, both included. Due-date reminders still go. It
 * changes only their own; nobody else's dates are shown here or anywhere.
 *
 * **"내 Google 캘린더에도 추가" (the user, 2026-10-06)** is drawn only for a
 * person whose own calendar is connected, and is off until they tick it: when
 * someone is away is theirs alone, so the dates reach Google by their own
 * press and no other way. The line under it says what is written -- one
 * all-day event titled 휴가, private -- before they tick, and the answer to
 * the save says whether it went. A save without the tick takes an earlier
 * event off again; so does 해제.
 */

/** What the save did on the calendar, said back; `critical` ones did not go. */
const CALENDAR_NOTE: Partial<Record<LeaveCalendarOutcome, { text: string; critical: boolean }>> = {
  added: { text: "내 Google 캘린더에 휴가 일정을 넣었습니다.", critical: false },
  removed: { text: "내 Google 캘린더에서 휴가 일정을 지웠습니다.", critical: false },
  removal_queued: {
    text: "캘린더의 휴가 일정을 지금은 지우지 못했습니다. 다시 시도하며, 캘린더에서 직접 지워도 됩니다.",
    critical: true,
  },
  not_connected: {
    text: "기간은 저장했습니다. 캘린더가 연결되어 있지 않아 캘린더에는 넣지 못했습니다.",
    critical: true,
  },
  // An event that is there and can no longer be reached: not "넣지 못했습니다".
  not_removed: {
    text: "캘린더가 연결되어 있지 않아 이전 휴가 일정을 지우지 못했습니다. 캘린더에서 직접 지워 주세요.",
    critical: true,
  },
  failed: {
    text: "기간은 저장했습니다. 캘린더에는 넣지 못했으니 잠시 후 다시 저장해 주세요.",
    critical: true,
  },
};

/**
 * An event of theirs is on the calendar and the calendar is not connected
 * just now (the user, 2026-10-07). Three true lines used to read crossed
 * here -- "들어가 있습니다", "연결하면 넣을지 고를 수 있습니다" and, after a
 * save, "넣지 못했습니다" -- so the state is said once, and the save's line
 * agrees with it: the event is still there, on the range it had.
 */
const STANDS_UNREACHED =
  "내 Google 캘린더에 넣어 둔 휴가 일정이 있지만, 지금은 캘린더가 연결되어 있지 않아 그 일정을 옮기거나 지울 수 없습니다. 다시 연결한 뒤 저장하면 이 기간으로 옮겨집니다.";
const SAVED_EVENT_LEFT =
  "기간은 저장했습니다. 캘린더가 연결되어 있지 않아 캘린더의 휴가 일정은 이전 기간 그대로입니다.";

export function NotificationPauseSetting() {
  const [saved, setSaved] = useState<NotificationPauseRead | null>(null);
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [onCalendar, setOnCalendar] = useState(false);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  const show = (answer: NotificationPauseRead) => {
    setSaved(answer);
    setFirst(answer.starts_on ?? "");
    setLast(answer.ends_on ?? "");
    // The box shows what stands: ticked only while an event of ours is there.
    setOnCalendar(answer.on_calendar === true);
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
  const connected = saved.calendar_connected === true;
  const stands = saved.on_calendar === true;
  const baseNote = saved.calendar ? CALENDAR_NOTE[saved.calendar] : undefined;
  // "넣지 못했습니다" is about an event that is not there. When one stands,
  // the save could not move it: say that instead.
  const calendarNote =
    saved.calendar === "not_connected" && stands && baseNote !== undefined
      ? { ...baseNote, text: SAVED_EVENT_LEFT }
      : baseNote;

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
          휴가 기간 (아침 요약, 월요일 요약, 오늘 업무 보고 초안, 회의 직후 알림을 받지
          않음. 월요일이 공휴일이라 다른 날 오는 월요일 요약도 같습니다)
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
          onClick={() =>
            send(
              // The field is sent only by a person who was shown the box.
              // Left out, the server leaves the calendar as it stands: a
              // person whose calendar is not connected just now unticked
              // nothing, and an event of theirs keeps its id for later.
              connected
                ? { starts_on: first, ends_on: last, on_calendar: onCalendar }
                : { starts_on: first, ends_on: last },
            )
          }
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
        {/* An event of theirs stands. Not "이 기간은": a save the calendar
            could not follow leaves it on the range it had. With no calendar
            connected the line below says it instead, with what that means. */}
        {stands && connected ? " 내 Google 캘린더에도 휴가 일정이 들어가 있습니다." : ""}
      </span>
      {connected ? (
        <>
          <label
            className="flex items-center gap-2 text-[var(--color-ink-body)]"
            style={meta}
          >
            <input
              type="checkbox"
              checked={onCalendar}
              disabled={saving}
              onChange={(event) => setOnCalendar(event.target.checked)}
            />
            내 Google 캘린더에도 추가
          </label>
          <span className="text-[var(--color-ink-muted)]" style={meta}>
            체크하고 저장하면 이 기간이 내 Google 캘린더에 &lsquo;휴가&rsquo;라는 종일
            일정으로 들어갑니다. 비공개 일정이라 내 캘린더를 공유받은 사람에게는 그
            날 바쁘다는 것만 보입니다. 체크를 풀고 저장하거나 기간을 해제하면 그 일정을
            지우고, 기간이 지난 일정은 내 캘린더에 그대로 남습니다.
          </span>
        </>
      ) : (
        <span className="text-[var(--color-ink-muted)]" style={meta}>
          {stands
            ? STANDS_UNREACHED
            : "내 Google 캘린더를 연결하면 이 기간을 캘린더에도 넣을지 고를 수 있습니다."}
        </span>
      )}
      {calendarNote !== undefined ? (
        <span
          role={calendarNote.critical ? "alert" : "status"}
          className={
            calendarNote.critical
              ? "text-[var(--color-signal-critical)]"
              : "text-[var(--color-ink-muted)]"
          }
          style={meta}
        >
          {calendarNote.text}
        </span>
      ) : null}
      <span className="text-[var(--color-ink-muted)]" style={meta}>
        공휴일에는 보내지 않습니다.
        {saved.calendar_leave
          ? " 연결한 Google 캘린더에 '부재중' 일정이 있는 시간에도 보내지 않습니다. 이를 위해서는 부재중 일정의 시간만 읽습니다."
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
