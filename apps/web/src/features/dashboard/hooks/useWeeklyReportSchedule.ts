"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/shared/api/client";
import { onAgentActed } from "@/shared/lib/agentActed";

import { getWeeklyReportSchedule, setWeeklyReportSchedule } from "../api";
import type { WeeklyReportSchedule } from "../types";

type Choice = Pick<WeeklyReportSchedule, "weekday" | "hour" | "send_empty">;

/**
 * When the team's weekly report goes out, and a member's change to it (#227).
 * Loaded on its own, so a failure here leaves the rest of the dashboard standing.
 * Read again when the assistant changed something (#1055): a schedule asked for
 * in chat is saved on the server, and the card showed the old one until a reload.
 */
export function useWeeklyReportSchedule(teamId: string) {
  const [schedule, setSchedule] = useState<WeeklyReportSchedule | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acted, setActed] = useState(0);

  useEffect(() => onAgentActed(() => setActed((n) => n + 1)), []);

  useEffect(() => {
    let live = true;
    getWeeklyReportSchedule(teamId)
      .then((loaded) => {
        if (live) {
          setSchedule(loaded);
          setError(null);
        }
      })
      .catch((reason) => {
        if (live) setError(readError(reason));
      });
    return () => {
      live = false;
    };
  }, [teamId, acted]);

  /** Saves a change; resolves to an error message, or null when it was saved. */
  const save = useCallback(
    async (choice: Choice): Promise<string | null> => {
      try {
        setSchedule(await setWeeklyReportSchedule(teamId, choice));
        return null;
      } catch (reason) {
        return reason instanceof ApiError && reason.status === 403
          ? "이 팀의 설정을 바꿀 권한이 없습니다."
          : "저장하지 못했습니다. 잠시 후 다시 시도해 주세요.";
      }
    },
    [teamId],
  );

  return { schedule, error, save };
}

function readError(reason: unknown): string {
  return reason instanceof ApiError && reason.status === 403
    ? "이 팀의 설정을 볼 권한이 없습니다."
    : "주간 리포트 설정을 불러오지 못했습니다.";
}
