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
        마감 알림 받기 (마감 전날과 마감이 지난 뒤 한 번, Slack DM)
      </label>
      {!setting.sent_here && (
        <span className="text-[var(--color-ink-muted)]" style={meta}>
          이 서버는 아직 마감 알림을 보내지 않습니다. 켜 두면 보내기 시작할
          때부터 받습니다.
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
