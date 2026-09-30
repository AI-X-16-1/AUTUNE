"use client";

import type { Route } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import { MaskedText, Row, StatusDot } from "@/shared/ui";

import type {
  AgendaItemRead,
  BriefMatchReason,
  BriefRead,
  ChangeType,
} from "../types";

/** Only the two changes the Slack brief tags; a new or re-affirmed decision carries no tag. */
const CHANGE_TAG: Partial<Record<ChangeType, string>> = {
  modified: "수정",
  reversed: "번복",
};

const MATCH_REASON: Record<BriefMatchReason, string> = {
  series: "같은 제목의 지난 회의",
  topic: "주제가 가까운 회의",
  latest: "가장 최근 회의",
};

/** Korea keeps no daylight saving and no team timezone exists yet — same as `dates.py`. */
const START_FORMAT = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  month: "long",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** `YYYY-MM-DD`, already a KST calendar day on the server; no timezone math. */
function koreanDay(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  return `${year}년 ${month}월 ${date}일`;
}

function Heading({ children }: { children: ReactNode }) {
  return (
    <header
      className="text-[var(--color-ink-strong)]"
      style={{
        fontSize: "var(--text-status)",
        fontWeight: "var(--text-status-weight)",
      }}
    >
      {children}
    </header>
  );
}

function Muted({ children }: { children: ReactNode }) {
  return (
    <p
      className="text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-rowBody)" }}
    >
      {children}
    </p>
  );
}

function AgendaRow({ item }: { item: AgendaItemRead }) {
  const key =
    item.key && item.url ? (
      <a href={item.url} target="_blank" rel="noreferrer" className="underline">
        {item.key}
      </a>
    ) : (
      item.key
    );
  return (
    <Row
      dot={<StatusDot variant="idle" hollow />}
      title={item.title}
      meta={
        key || item.status ? (
          <>
            {key}
            {key && item.status && <span aria-hidden="true"> · </span>}
            {item.status}
          </>
        ) : undefined
      }
    />
  );
}

function BriefBody({ brief }: { brief: BriefRead }) {
  const { recap } = brief;
  const starts = brief.starts_at
    ? START_FORMAT.format(new Date(brief.starts_at))
    : null;

  return (
    <section
      aria-label="회의 전 브리프"
      style={{ display: "grid", gap: "var(--space-16)" }}
    >
      <div>
        <Heading>회의 전 브리프</Heading>
        <p
          className="text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-meta)" }}
        >
          {[
            starts && `${starts} 시작`,
            brief.sent_at
              ? "Slack으로 보냄"
              : "Slack 미연결 — 여기서만 볼 수 있습니다",
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>

      <div>
        <Heading>지난 회의</Heading>
        {recap ? (
          <>
            <Row
              dot={<StatusDot variant="confirmed" />}
              title={
                <Link
                  href={`/meetings/${recap.meeting_id}/context` as Route}
                  className="underline"
                >
                  {recap.title}
                </Link>
              }
              meta={[
                recap.day && koreanDay(recap.day),
                brief.match_reason && MATCH_REASON[brief.match_reason],
              ]
                .filter(Boolean)
                .join(" · ")}
            />
            {recap.topics.length > 0 && (
              <div className="mt-[var(--space-16)]">
                <Muted>다룬 주제</Muted>
                {recap.topics.map((topic) => (
                  <Row
                    key={topic}
                    dot={<StatusDot variant="idle" />}
                    title={<MaskedText>{topic}</MaskedText>}
                  />
                ))}
              </div>
            )}
            <div className="mt-[var(--space-16)]">
              <Muted>결정</Muted>
              {recap.decisions.length > 0 ? (
                recap.decisions.map((decision, index) => {
                  const tag = CHANGE_TAG[decision.change_type];
                  return (
                    <Row
                      // Two decisions can read the same; position is the identity here.
                      key={index}
                      dot={
                        <StatusDot
                          variant={tag ? "attention" : "confirmed"}
                          hollow={decision.change_type === "new"}
                        />
                      }
                      title={<MaskedText>{decision.statement}</MaskedText>}
                      meta={tag}
                    />
                  );
                })
              ) : (
                <Muted>지난 회의에서 기록된 결정이 없습니다.</Muted>
              )}
            </div>
          </>
        ) : brief.recap_gone ? (
          // The retention sweep took it; privacy.md forbids rebuilding it from anything left.
          <Muted>지난 회의는 보존 기간이 지나 삭제되었습니다.</Muted>
        ) : (
          <Muted>참고할 지난 회의가 없습니다.</Muted>
        )}
      </div>

      <div>
        <Heading>이번 회의에서 다룰 문제</Heading>
        {brief.agenda.length > 0 ? (
          brief.agenda.map((item, index) => (
            <AgendaRow key={item.key ?? index} item={item} />
          ))
        ) : (
          <Muted>이번 회의에 연결된 안건이 없습니다.</Muted>
        )}
      </div>
    </section>
  );
}

/**
 * S09's brief, in the app — the same recap the Slack message carries, above
 * S15's context tab.
 *
 * Takes the result of `useBrief` rather than fetching it, because the tab
 * below needs the same answer: with a brief showing, its "no linked meetings"
 * line is noise on a meeting that has not happened yet.
 *
 * Draws nothing when the meeting has no brief (a 404 is the ordinary case: a
 * finished meeting never had one, a scheduled one gets it ten minutes before
 * the start) and nothing while it loads, so the tab does not jump for the
 * common meeting that has none.
 */
export function BriefPanel({
  brief,
  error,
}: {
  brief: BriefRead | null;
  error: Error | null;
}) {
  if (error) {
    return (
      <p
        className="text-[var(--color-signal-critical)]"
        style={{ fontSize: "var(--text-rowBody)" }}
      >
        브리프를 불러오지 못했습니다.
      </p>
    );
  }
  return brief ? <BriefBody brief={brief} /> : null;
}
