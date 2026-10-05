"use client";

import Link from "next/link";
import type { Route } from "next";
import { useState } from "react";

import { Button, MaskedText, ScoreLabel, StatusDot } from "@/shared/ui";

import { useTeamGaps } from "../hooks/useTeamGaps";
import { SEVERITY_LABELS } from "../types";
import type { TeamGap } from "../types";

/**
 * The sidebar's "갭 리포트": every open gap across the team's meetings (#550).
 *
 * Grouped by meeting, newest first, each group linking to that meeting's S20 —
 * which is where a gap's why lives. HIGH alone by default, the precision rule
 * S20 keeps; MEDIUM and LOW are one toggle away, not gone.
 *
 * **No people anywhere on this screen.** A row is a gap, its score and the
 * question that would close it. Who was silent on the topic behind it is on the
 * meeting's report, read along that topic; a list across every meeting is where
 * the same ids would total up along a person (privacy.md section 3).
 *
 * "해당 없음" is not here on purpose: dismissing a gap is a judgement made with
 * its evidence in view, and the evidence is on the meeting's report.
 */
export function TeamGapList({ teamId }: { teamId: string }) {
  const [showAll, setShowAll] = useState(false);
  const { gaps, loading, error, reload } = useTeamGaps(teamId, showAll);

  return (
    <section className="flex flex-col" style={{ gap: "var(--space-16)" }}>
      <header className="flex flex-wrap items-end justify-between" style={{ gap: "var(--space-8)" }}>
        <div>
          <h1
            className="text-[var(--color-ink-strong)]"
            style={{ fontSize: "var(--text-title)", fontWeight: "var(--text-title-weight)" }}
          >
            갭 리포트
          </h1>
          <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
            팀의 회의에서 아직 닫히지 않은 갭입니다. 최근 회의부터 보여 줍니다.
          </p>
        </div>
        <Button tone="text" size="compact" onClick={() => setShowAll((value) => !value)}>
          {showAll
            ? `${SEVERITY_LABELS.high}만 보기`
            : `${SEVERITY_LABELS.medium}·${SEVERITY_LABELS.low}도 보기`}
        </Button>
      </header>

      {loading && gaps === null ? <Muted>불러오는 중…</Muted> : null}

      {error ? (
        <div className="flex items-center" style={{ gap: "var(--space-8)" }}>
          <p className="text-[var(--color-signal-critical)]" style={{ fontSize: "var(--text-rowBody)" }}>
            갭 목록을 불러오지 못했습니다.
          </p>
          <Button tone="text" size="compact" onClick={() => void reload()}>
            다시 시도
          </Button>
        </div>
      ) : null}

      {gaps !== null && gaps.length === 0 ? (
        <Muted>
          {showAll
            ? "열린 갭이 없습니다."
            : `열린 ${SEVERITY_LABELS.high} 갭이 없습니다. ${SEVERITY_LABELS.medium}·${SEVERITY_LABELS.low} 갭은 위에서 볼 수 있습니다.`}
        </Muted>
      ) : null}

      {gaps !== null && gaps.length > 0
        ? byMeeting(gaps).map((group) => <MeetingGroup key={group.meetingId} group={group} />)
        : null}
    </section>
  );
}

interface Group {
  meetingId: string;
  title: string;
  date: string;
  gaps: TeamGap[];
}

/** Consecutive rows of one meeting. The server already sorts by meeting. */
function byMeeting(gaps: readonly TeamGap[]): Group[] {
  const groups: Group[] = [];
  for (const gap of gaps) {
    const last = groups.at(-1);
    if (last?.meetingId === gap.meeting_id) last.gaps.push(gap);
    else
      groups.push({
        meetingId: gap.meeting_id,
        title: gap.meeting_title,
        date: gap.meeting_date,
        gaps: [gap],
      });
  }
  return groups;
}

function MeetingGroup({ group }: { group: Group }) {
  const { meetingId, title, date, gaps } = group;
  return (
    <section>
      <Link
        href={`/meetings/${meetingId}/gap` as Route}
        className="flex items-baseline hover:underline"
        style={{ gap: "var(--space-8)", paddingBottom: "var(--space-4)" }}
      >
        <span
          className="text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-rowTitle)", fontWeight: "var(--text-rowTitle-weight)" }}
        >
          {title}
        </span>
        <span className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
          {date.slice(0, 10)} · {gaps.length}건
        </span>
      </Link>
      <ul className="border-t border-[var(--color-hairline)]">
        {gaps.map((gap) => (
          <GapRow key={gap.gap_id} gap={gap} />
        ))}
      </ul>
    </section>
  );
}

function GapRow({ gap }: { gap: TeamGap }) {
  return (
    <li
      className="grid border-b border-[var(--color-hairline)]"
      style={{ gridTemplateColumns: "auto 1fr", gap: "var(--space-12)", padding: "10px 0" }}
    >
      <StatusDot variant={DOT[gap.severity]} className="mt-2" />
      {/* The score wraps under the title when the row is narrow, as on S20's
          cards, rather than squeezing the title to a character per line. */}
      <span
        className="flex min-w-0 flex-wrap items-start justify-between"
        style={{ columnGap: "var(--space-12)", rowGap: "var(--space-4)" }}
      >
        <span className="min-w-0 flex-1" style={{ flexBasis: "12rem" }}>
          <span
            className="block text-[var(--color-ink-strong)]"
            style={{ fontSize: "var(--text-rowBody)" }}
          >
            <MaskedText>{gap.title}</MaskedText>
          </span>
          {gap.suggested_question ? (
            <span
              className="block text-[var(--color-ink-muted)]"
              style={{ fontSize: "var(--text-metaSmall)" }}
            >
              <MaskedText>{gap.suggested_question}</MaskedText>
            </span>
          ) : null}
        </span>
        <ScoreLabel level={gap.severity} score={gap.risk_score} />
      </span>
    </li>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-rowBody)" }}>
      {children}
    </p>
  );
}

const DOT = { high: "critical", medium: "attention", low: "idle" } as const;
