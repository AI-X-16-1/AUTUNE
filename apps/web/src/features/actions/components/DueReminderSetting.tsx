"use client";

import { useEffect, useState } from "react";

import {
  getDueReminders,
  setDueReminders,
  type DueReminderSetting as Setting,
} from "../api";

/**
 * The person's own switch for due-date reminders (review of #751): a Slack DM
 * the day before an item of theirs is due and once after it passes. On unless
 * they turn it off. It changes only their own; nobody else's is shown here.
 *
 * The same switch governs the Monday digest and the morning DM. The Monday
 * digest goes on the week's first working day when Monday is a public holiday
 * (#850), and the label says so.
 */
export function DueReminderSetting() {
  const [setting, setSetting] = useState<Setting | null>(null);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    getDueReminders()
      .then((answer) => {
        if (alive) setSetting(answer);
      })
      .catch(() => {
        // Not shown at all rather than shown wrong: the rest of the page works.
      });
    return () => {
      alive = false;
    };
  }, []);

  if (setting === null) return null;
  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  // One switch, four messages, and a server turns each on by itself: the
  // line under the switch says which of them this one sends. It used to read
  // the reminder's flag alone and say "none yet" on a server sending both
  // digests (dev, 2026-10-05).
  const kinds: [string, boolean][] = [
    ["마감 알림", setting.sent_here],
    ["월요일 요약", setting.weekly_here],
    ["아침 요약", setting.daily_here],
    ["오늘 업무 보고", setting.work_report_here === true],
  ];
  const sent = kinds.filter(([, here]) => here).map(([name]) => name);
  const unsent = kinds.filter(([, here]) => !here).map(([name]) => name);

  const change = (on: boolean) => {
    setSaving(true);
    setFailed(false);
    setDueReminders(on)
      .then(setSetting)
      .catch(() => setFailed(true))
      .finally(() => setSaving(false));
  };

  return (
    <div className="flex flex-col gap-1">
      <label
        className="flex items-center gap-2 text-[var(--color-ink-body)]"
        style={meta}
      >
        <input
          type="checkbox"
          checked={setting.on}
          disabled={saving}
          onChange={(event) => change(event.target.checked)}
        />
        마감 알림 받기 (마감 전날과 마감이 지난 뒤 한 번, 월요일의 내 할 일
        요약 — 월요일이 공휴일이면 그 주의 첫 평일 —, 화~금 아침 요약, 평일
        오후의 오늘 업무 보고 초안 · Slack DM)
      </label>
      {unsent.length > 0 && (
        <span className="text-[var(--color-ink-muted)]" style={meta}>
          {sent.length === 0
            ? "이 서버는 아직 이 알림들을 보내지 않습니다."
            : `이 서버는 지금 ${sent.join(", ")}만 보냅니다. 아직 보내지 않는 것: ${unsent.join(", ")}.`}{" "}
          켜 두면 보내기 시작할 때부터 받습니다.
        </span>
      )}
      {failed && (
        <span
          role="alert"
          className="text-[var(--color-signal-critical)]"
          style={meta}
        >
          설정을 바꾸지 못했습니다. 다시 시도해 주세요.
        </span>
      )}
    </div>
  );
}
