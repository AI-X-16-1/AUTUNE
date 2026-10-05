"use client";

import { useState } from "react";

import { Button } from "@/shared/ui";

import { DashboardCard } from "./DashboardCard";
import { useWeeklyReportSchedule } from "../hooks/useWeeklyReportSchedule";
import type { WeeklyReportSchedule } from "../types";

export const WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"] as const;

type Choice = Pick<WeeklyReportSchedule, "weekday" | "hour" | "send_empty">;

/** When the team's weekly report goes to its Slack channel; any member changes it (#227). */
export function WeeklyReportScheduleCard({ teamId }: { teamId: string }) {
  const { schedule, error, save } = useWeeklyReportSchedule(teamId);
  return <WeeklyReportScheduleView schedule={schedule} error={error} onSave={save} />;
}

export function WeeklyReportScheduleView({
  schedule,
  error,
  onSave,
}: {
  schedule: WeeklyReportSchedule | null;
  error: string | null;
  onSave: (choice: Choice) => Promise<string | null>;
}) {
  const [draft, setDraft] = useState<Choice | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  if (error) {
    return (
      <DashboardCard title="주간 리포트">
        <p style={metaStyle}>{error}</p>
      </DashboardCard>
    );
  }
  if (!schedule) return null;

  if (draft == null) {
    return (
      <DashboardCard title="주간 리포트">
        <p style={{ ...metaStyle, color: "var(--color-ink-strong)" }}>
          {describe(schedule)}
        </p>
        <p style={{ ...metaStyle, marginTop: "var(--space-8)" }}>
          {schedule.send_empty
            ? "회의도 밀린 항목도 없는 주에도 보냅니다."
            : "회의도 밀린 항목도 없는 주에는 보내지 않습니다."}
          {schedule.updated_by_name && schedule.updated_at
            ? ` · ${schedule.updated_by_name}님이 ${formatTime(schedule.updated_at)}에 바꿈`
            : ""}
        </p>
        <div style={{ marginTop: "var(--space-8)" }}>
          <Button
            tone="text"
            size="compact"
            onClick={() => {
              setFailure(null);
              setDraft({
                weekday: schedule.weekday,
                hour: schedule.hour,
                send_empty: schedule.send_empty,
              });
            }}
          >
            바꾸기
          </Button>
        </div>
      </DashboardCard>
    );
  }

  const submit = async () => {
    setBusy(true);
    const message = await onSave(draft);
    setBusy(false);
    if (message) setFailure(message);
    else setDraft(null);
  };

  return (
    <DashboardCard title="주간 리포트">
      <div className="flex" style={{ gap: "var(--space-8)", alignItems: "center" }}>
        <select
          aria-label="요일"
          value={draft.weekday}
          onChange={(event) => setDraft({ ...draft, weekday: Number(event.target.value) })}
          className="border bg-transparent"
        >
          {WEEKDAYS.map((name, index) => (
            <option key={name} value={index}>
              {name}요일
            </option>
          ))}
        </select>
        <select
          aria-label="시각"
          value={draft.hour}
          onChange={(event) => setDraft({ ...draft, hour: Number(event.target.value) })}
          className="border bg-transparent"
        >
          {Array.from({ length: 24 }, (_, hour) => (
            <option key={hour} value={hour}>
              {pad(hour)}:00
            </option>
          ))}
        </select>
        <span style={metaStyle}>한국 시간</span>
      </div>
      <label className="flex" style={{ ...metaStyle, gap: "var(--space-8)", marginTop: "var(--space-8)" }}>
        <input
          type="checkbox"
          checked={draft.send_empty}
          onChange={(event) => setDraft({ ...draft, send_empty: event.target.checked })}
        />
        회의도 밀린 항목도 없는 주에도 보내기
      </label>
      {failure ? (
        <p style={{ ...metaStyle, color: "var(--color-signal-critical)", marginTop: "var(--space-8)" }}>
          {failure}
        </p>
      ) : null}
      <div className="flex" style={{ gap: "var(--space-8)", marginTop: "var(--space-8)" }}>
        <Button tone="primary" size="compact" onClick={submit} disabled={busy}>
          저장
        </Button>
        <Button tone="text" size="compact" onClick={() => setDraft(null)} disabled={busy}>
          취소
        </Button>
      </div>
    </DashboardCard>
  );
}

function describe(schedule: WeeklyReportSchedule): string {
  return `매주 ${WEEKDAYS[schedule.weekday]}요일 ${pad(schedule.hour)}:00(한국 시간)에 팀 Slack 채널로 보냅니다.`;
}

function pad(hour: number): string {
  return String(hour).padStart(2, "0");
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const metaStyle = { margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" };
