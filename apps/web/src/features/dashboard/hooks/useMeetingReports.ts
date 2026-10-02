"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/shared/api/client";

import { correctMeetingReport, editMeetingReport, getMeetingReports } from "../api";
import type { MeetingReport } from "../types";

/**
 * The dashboard's meeting reports and the card's two writes: a member edits a
 * draft, or writes a correction to a posted one. Both go to the approval queue
 * (#674); nothing is posted from the card. Loaded on its own, apart
 * from the S26 rollup, so a failure here leaves the rest of the dashboard
 * standing.
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
  const save = useCallback(async (report: MeetingReport, body: string): Promise<string | null> => {
    try {
      const saved = await editMeetingReport(report.meeting_id, body, report.updated_at);
      setReports((current) => current.map((r) => (r.meeting_id === saved.meeting_id ? saved : r)));
      return null;
    } catch (reason) {
      return writeErrorMessage(reason);
    }
  }, []);

  /** Stores a correction; resolves to an error message, or null once it waits for approval. */
  const correct = useCallback(async (meetingId: string, body: string): Promise<string | null> => {
    try {
      const saved = await correctMeetingReport(meetingId, body);
      setReports((current) => current.map((r) => (r.meeting_id === saved.meeting_id ? saved : r)));
      return null;
    } catch (reason) {
      return writeErrorMessage(reason);
    }
  }, []);

  return { reports, loading, error, reload, save, correct };
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

function writeErrorMessage(reason: unknown): string {
  if (!(reason instanceof ApiError)) return "처리하지 못했습니다. 잠시 후 다시 시도해주세요.";
  if (reason.status === 409) {
    if (reason.message.includes("changed since")) {
      return "다른 사람이 먼저 고쳤습니다. 새로고침한 뒤 다시 고쳐 주세요.";
    }
    if (reason.message.includes("still being sent")) {
      return "승인된 수정본을 보내는 중입니다. 잠시 후 다시 시도해주세요.";
    }
    if (reason.message.includes("not posted")) return "게시 전 초안은 편집으로 고칩니다.";
    if (reason.message.includes("did not reach slack")) {
      return "이 리포트는 Slack에 올라가지 않아 수정본을 보낼 수 없습니다.";
    }
    if (reason.message.includes("slack is not connected")) {
      return "팀의 Slack 연결이나 채널 설정이 없어 수정본을 보낼 수 없습니다. 설정에서 다시 연결해 주세요.";
    }
    return "이미 게시된 리포트입니다.";
  }
  if (reason.status === 404) return "리포트를 찾을 수 없습니다.";
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
    if (reason.message.includes("exceeds")) {
      return "3,000자를 넘어 저장하지 않았습니다 (&, <, >는 Slack에서 여러 글자로 셉니다).";
    }
    if (reason.message.includes("empty")) return "내용이 비어 있습니다.";
    if (reason.message.includes("unchanged")) return "바뀐 내용이 없어 저장하지 않았습니다.";
  }
  return "처리하지 못했습니다. 잠시 후 다시 시도해주세요.";
}
