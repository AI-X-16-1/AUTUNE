"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { listDecided } from "../api";
import { REASONS, SUBAGENT_LABEL } from "../approvalLabels";
import { meetingLabel } from "../meetingLabel";
import { reportLink } from "../reportLink";
import type { DecidedAction } from "../types";

const LOAD_FAILED = "불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";

/** The result a decided card states, and the signal colour it is drawn in. */
export function decidedResult(item: DecidedAction): {
  label: string;
  color: string;
} {
  if (item.status === "rejected") {
    const reason = REASONS.find((r) => r.value === item.reject_reason)?.label;
    return {
      label: reason ? `거절됨 · ${reason}` : "거절됨",
      color: "var(--color-signal-idle)",
    };
  }
  if (item.status === "failed") {
    return {
      label: "승인됨 · 실행 못 함",
      color: "var(--color-signal-critical)",
    };
  }
  if (item.needs_check) {
    return {
      label: "확인 필요 — 결과가 기록되지 않았습니다",
      color: "var(--color-signal-attention)",
    };
  }
  return { label: "승인됨 · 실행됨", color: "var(--color-signal-confirmed)" };
}

/** "10월 11일(토) 14:05", in Korea's time. */
export function decidedWhen(iso: string): string {
  const parts = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "numeric",
    day: "numeric",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value;
  return `${get("month")}월 ${get("day")}일(${get("weekday")}) ${get("hour")}:${get("minute")}`;
}

/**
 * 처리한 승인 (#1215) — what the person's approvers decided in the last 30
 * days, newest first: the card as it waited, then who decided, when, and how
 * it went. The server applies the waiting list's rule, so only proposals the
 * person may decide are here. Read only; nothing is decided again from here.
 */
export function DecidedApprovals({ teamId }: { teamId: string | null }) {
  const [items, setItems] = useState<DecidedAction[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    listDecided()
      .then((list) => current && setItems(list))
      // Never the API's own message: it is English and meant for a developer.
      .catch(() => current && setError(LOAD_FAILED));
    return () => {
      current = false;
    };
  }, []);

  // The team being looked at, and only it (the user, 2026-10-13). Nothing is
  // listed until the team is known, so another team's cards never flash by.
  const shown: DecidedAction[] | null =
    teamId === null || items === null
      ? null
      : items.filter((item) => item.team_id === teamId);

  return (
    <>
      {error ? (
        <p role="alert" style={{ color: "var(--color-signal-critical)" }}>
          {error}
        </p>
      ) : null}
      {shown === null && !error ? (
        <p className="mt-4 text-[var(--color-ink-muted)]">불러오는 중…</p>
      ) : null}
      {shown?.length === 0 ? (
        <p className="mt-4 text-[var(--color-ink-muted)]">
          최근 30일 동안 이 팀에서 처리한 제안이 없습니다.
        </p>
      ) : null}
      <ul className="mt-4 flex flex-col gap-4">
        {shown?.map((item) => {
          const result = decidedResult(item);
          return (
            <li
              key={item.id}
              className="rounded-[var(--radius)] border border-[var(--color-hairline)] p-4"
              style={{ background: "var(--color-surface-panel)" }}
            >
              <div
                className="text-[var(--color-ink-muted)]"
                style={{ fontSize: "var(--text-metaSmall)" }}
              >
                {SUBAGENT_LABEL[item.subagent] ?? item.subagent}
                {item.meeting_id ? (
                  <>
                    {" · "}
                    <Link
                      href={`/meetings/${item.meeting_id}`}
                      className="text-[var(--color-accent-default)]"
                    >
                      {meetingLabel(item)}
                    </Link>
                  </>
                ) : null}
                {reportLink(item) ? (
                  <>
                    {" · "}
                    <Link
                      href={reportLink(item)!}
                      className="text-[var(--color-accent-default)]"
                    >
                      대시보드에서 리포트 보기
                    </Link>
                  </>
                ) : null}
              </div>
              <h2
                className="mt-1 text-[var(--color-ink-strong)]"
                style={{ fontSize: "var(--text-rowTitle)" }}
              >
                {item.title}
              </h2>
              <pre
                className="mt-2 whitespace-pre-wrap font-sans"
                style={{ fontSize: "var(--text-meta)" }}
              >
                {item.body}
              </pre>
              <p
                className="mt-3 flex flex-wrap items-baseline justify-between gap-2"
                style={{ fontSize: "var(--text-meta)" }}
              >
                <span style={{ color: result.color, fontWeight: 600 }}>
                  {result.label}
                </span>
                <span className="text-[var(--color-ink-muted)]">
                  {item.decided_by_name ?? "알 수 없는 사용자"}
                  {item.decided_at ? ` · ${decidedWhen(item.decided_at)}` : ""}
                </span>
              </p>
            </li>
          );
        })}
      </ul>
    </>
  );
}
