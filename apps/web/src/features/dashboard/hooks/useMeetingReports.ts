"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/shared/api/client";

import { editMeetingReport, getMeetingReports } from "../api";
import type { MeetingReport } from "../types";

/**
 * The dashboard's meeting reports and the one write the card makes: a draft's
 * body, edited before it is posted. Loaded on its own, apart from the S26
 * rollup, so a failure here leaves the rest of the dashboard standing.
 */
export function useMeetingReports(teamId: string) {
  const [reports, setReports] = useState<MeetingReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setReports(await getMeetingReports(teamId));
      setError(null);
    } catch (reason) {
      setError(
        reason instanceof ApiError && reason.status === 403
          ? "이 팀의 회의 리포트를 볼 권한이 없습니다."
          : "회의 리포트를 불러오지 못했습니다.",
      );
    } finally {
      setLoading(false);
    }
  }, [teamId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** Saves an edit; resolves to an error message, or null when it was saved. */
  const save = useCallback(async (meetingId: string, body: string): Promise<string | null> => {
    try {
      const saved = await editMeetingReport(meetingId, body);
      setReports((current) => current.map((r) => (r.meeting_id === meetingId ? saved : r)));
      return null;
    } catch (reason) {
      return editErrorMessage(reason);
    }
  }, []);

  return { reports, loading, error, reload, save };
}

/** `autune_integrations.privacy` category names, as a person reads them. */
const CATEGORY_NAMES: Record<string, string> = {
  phone: "전화번호",
  rrn: "주민등록번호",
  card: "카드번호",
  account: "계좌번호",
  digits: "긴 숫자열",
  email: "이메일",
};

function editErrorMessage(reason: unknown): string {
  if (!(reason instanceof ApiError)) return "저장하지 못했습니다. 잠시 후 다시 시도해주세요.";
  if (reason.status === 409) return "이미 게시된 리포트라 여기서 고칠 수 없습니다.";
  if (reason.status === 403) return "이 팀의 리포트를 고칠 권한이 없습니다.";
  if (reason.status === 422) {
    // The server names categories only, never the text (privacy.md section 2).
    const categories = /personal data: (.+)$/.exec(reason.message)?.[1];
    if (categories) {
      const names = categories
        .split(", ")
        .map((category) => CATEGORY_NAMES[category] ?? category)
        .join(", ");
      return `개인정보가 남아 있어 저장하지 않았습니다 (${names}).`;
    }
    if (reason.message.includes("exceeds")) return "리포트는 3,000자를 넘을 수 없습니다.";
    if (reason.message.includes("empty")) return "본문이 비어 있습니다.";
  }
  return "저장하지 못했습니다. 잠시 후 다시 시도해주세요.";
}
