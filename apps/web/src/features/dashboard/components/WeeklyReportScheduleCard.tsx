"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/shared/api/client";
import { getSession } from "@/shared/api/auth";
import { Button } from "@/shared/ui";

import { DashboardCard } from "./DashboardCard";
import { setWeeklyReportSchedule } from "../api";
import { useWeeklyReportSchedule } from "../hooks/useWeeklyReportSchedule";
import type { WeeklyReportSchedule } from "../types";

export const WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"] as const;

type Choice = Pick<WeeklyReportSchedule, "weekday" | "hour" | "send_empty">;

type Team = { id: string; name: string };

/** Which of the person's teams a change goes to. */
type Reach = "this" | "all" | "some";

/**
 * When the team's weekly report goes to its Slack channel; any member changes
 * it (#227). A person in several teams can give the same schedule to all of
 * them, or to the ones they pick: each is saved through the team's own route,
 * which checks membership as it always does.
 */
export function WeeklyReportScheduleCard({ teamId }: { teamId: string }) {
  const { schedule, error, save } = useWeeklyReportSchedule(teamId);
  const [teams, setTeams] = useState<Team[]>([]);
  useEffect(() => {
    let live = true;
    void getSession().then((user) => {
      if (live) setTeams(user?.teams ?? []);
    });
    return () => {
      live = false;
    };
  }, []);
  return (
    <WeeklyReportScheduleView
      schedule={schedule}
      error={error}
      onSave={save}
      teamId={teamId}
      teams={teams}
      onSaveTeam={saveTeam}
    />
  );
}

/** Saves another team's schedule; resolves to why it was not saved, or null. */
async function saveTeam(teamId: string, choice: Choice): Promise<string | null> {
  try {
    await setWeeklyReportSchedule(teamId, choice);
    return null;
  } catch (reason) {
    return reason instanceof ApiError && reason.status === 403
      ? "바꿀 권한이 없습니다"
      : "저장하지 못했습니다";
  }
}

export function WeeklyReportScheduleView({
  schedule,
  error,
  onSave,
  teamId,
  teams = [],
  onSaveTeam,
}: {
  schedule: WeeklyReportSchedule | null;
  error: string | null;
  /** Saves this team's schedule; resolves to an error message, or null. */
  onSave: (choice: Choice) => Promise<string | null>;
  teamId?: string;
  /** The person's teams. With more than one, the change can go to others too. */
  teams?: Team[];
  onSaveTeam?: (teamId: string, choice: Choice) => Promise<string | null>;
}) {
  const [draft, setDraft] = useState<Choice | null>(null);
  const [reach, setReach] = useState<Reach>("this");
  const [picked, setPicked] = useState<string[]>([]);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  if (error) {
    return (
      <DashboardCard title="주간 리포트">
        <p style={metaStyle}>{error}</p>
      </DashboardCard>
    );
  }
  if (!schedule) return null;

  const others = teams.filter((team) => team.id !== teamId);
  const canReach = others.length > 0 && onSaveTeam !== undefined;
  const targets: Team[] =
    reach === "all" ? others : reach === "some" ? others.filter((t) => picked.includes(t.id)) : [];

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
        {done ? (
          <p role="status" style={{ ...metaStyle, marginTop: "var(--space-8)" }}>
            {done}
          </p>
        ) : null}
        <div style={{ marginTop: "var(--space-8)" }}>
          <Button
            tone="text"
            size="compact"
            onClick={() => {
              setFailure(null);
              setDone(null);
              setReach("this");
              setPicked([]);
              setConfirming(false);
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

  const apply = async () => {
    setBusy(true);
    const own = await onSave(draft);
    const results = await Promise.all(
      targets.map(async (team) => ({ team, why: await onSaveTeam!(team.id, draft) })),
    );
    setBusy(false);
    setConfirming(false);
    const missed = results.filter((r) => r.why !== null);
    if (own !== null && targets.length === 0) {
      setFailure(own);
      return;
    }
    const total = targets.length + 1;
    const saved = total - missed.length - (own === null ? 0 : 1);
    if (missed.length === 0 && own === null) {
      setDraft(null);
      setDone(targets.length > 0 ? `${total}개 팀에 적용했습니다.` : null);
      return;
    }
    const reasons = [
      ...(own === null ? [] : [`이 팀: ${own}`]),
      ...missed.map((r) => `${r.team.name}: ${r.why}`),
    ];
    setFailure(`${total}개 팀 중 ${saved}개 팀을 바꿨습니다. ${reasons.join(" · ")}`);
  };

  const submit = () => {
    if (targets.length > 0) setConfirming(true);
    else void apply();
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
      {canReach ? (
        <fieldset style={{ border: 0, padding: 0, margin: "var(--space-8) 0 0" }}>
          <legend style={metaStyle}>적용할 팀</legend>
          <div className="flex" style={{ ...metaStyle, gap: "var(--space-12)", flexWrap: "wrap" }}>
            {(
              [
                ["this", "이 팀만"],
                ["all", "내 모든 팀"],
                ["some", "팀 골라서"],
              ] as const
            ).map(([value, label]) => (
              <label key={value} className="flex" style={{ gap: "var(--space-4)" }}>
                <input
                  type="radio"
                  name="schedule-reach"
                  checked={reach === value}
                  onChange={() => {
                    setReach(value);
                    setConfirming(false);
                  }}
                />
                {label}
              </label>
            ))}
          </div>
          {reach === "some" ? (
            <div className="flex" style={{ ...metaStyle, gap: "var(--space-12)", flexWrap: "wrap", marginTop: "var(--space-4)" }}>
              {others.map((team) => (
                <label key={team.id} className="flex" style={{ gap: "var(--space-4)" }}>
                  <input
                    type="checkbox"
                    aria-label={`${team.name}에도 적용`}
                    checked={picked.includes(team.id)}
                    onChange={(event) => {
                      setConfirming(false);
                      setPicked(
                        event.target.checked
                          ? [...picked, team.id]
                          : picked.filter((id) => id !== team.id),
                      );
                    }}
                  />
                  {team.name}
                </label>
              ))}
            </div>
          ) : null}
        </fieldset>
      ) : null}
      {failure ? (
        <p style={{ ...metaStyle, color: "var(--color-signal-critical)", marginTop: "var(--space-8)" }}>
          {failure}
        </p>
      ) : null}
      {confirming ? (
        <div role="alertdialog" aria-label="여러 팀에 적용" style={{ marginTop: "var(--space-8)" }}>
          <p style={{ ...metaStyle, color: "var(--color-ink-strong)" }}>
            이 팀과 {targets.map((t) => t.name).join(", ")}의 주간 리포트 시각이 모두 바뀝니다.
            다른 팀원도 이 시각을 씁니다.
          </p>
          <div className="flex" style={{ gap: "var(--space-8)", marginTop: "var(--space-8)" }}>
            <Button tone="primary" size="compact" onClick={() => void apply()} disabled={busy}>
              {targets.length + 1}개 팀에 적용
            </Button>
            <Button tone="text" size="compact" onClick={() => setConfirming(false)} disabled={busy}>
              돌아가기
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex" style={{ gap: "var(--space-8)", marginTop: "var(--space-8)" }}>
          <Button
            tone="primary"
            size="compact"
            onClick={submit}
            disabled={busy || (reach === "some" && targets.length === 0)}
          >
            저장
          </Button>
          <Button tone="text" size="compact" onClick={() => setDraft(null)} disabled={busy}>
            취소
          </Button>
        </div>
      )}
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
