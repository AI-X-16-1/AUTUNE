"use client";

import Link from "next/link";

import { ActionCompletionRate } from "./ActionCompletionRate";
import { AlignmentHeatmap } from "./AlignmentHeatmap";
import { DashboardCard } from "./DashboardCard";
import { GapDistributionBars } from "./GapDistributionBars";
import { MeetingReportsCard } from "./MeetingReportsCard";
import { PredictionCard } from "./PredictionCard";
import { QualityScoreCard } from "./QualityScoreCard";
import { WeeklyReportScheduleCard } from "./WeeklyReportScheduleCard";
import { useDashboard } from "../hooks/useDashboard";

/**
 * S26 — the team dashboard. No page route wires this up yet: `apps/web/src/app`
 * has no pages for any module, and `team_id` resolution needs the auth/team
 * context the whole app is still missing (#156, #189). A future page passes
 * `teamId` in once that lands.
 *
 * **No influence map here, not even as a placeholder.** The influence map goes
 * to the person themselves and nobody else, the way the speaking-ratio DM does
 * (#28, #304; ui-spec S26). A "Phase 2" card on a screen the whole team sees
 * would announce the opposite.
 */
export function Dashboard({ teamId }: { teamId: string }) {
  const { dashboard, heatmap, gapTitles, predictions, loading, error } = useDashboard(teamId);

  if (loading && !dashboard) {
    return <p style={metaStyle}>불러오는 중…</p>;
  }

  if (error) {
    return (
      <p style={{ ...metaStyle, color: "var(--color-signal-critical)" }}>
        불러오지 못했습니다. 잠시 후 다시 시도해주세요.
      </p>
    );
  }

  if (!dashboard) {
    return null;
  }

  // No meeting scored yet: every widget would say so on its own, so one card
  // says it once, beside the one thing a member can set already (#1179).
  if (dashboard.meeting_count === 0) {
    return (
      <div className="grid" style={gridStyle}>
        <div style={{ gridColumn: "1 / -1" }}>
          <DashboardCard title="대시보드">
            <p style={{ ...metaStyle, color: "var(--color-ink-strong)" }}>
              아직 분석된 회의가 없습니다.
            </p>
            <p style={{ ...metaStyle, marginTop: "var(--space-8)" }}>
              회의를 분석하면 품질 점수, 갭 유형, 할 일 완료율, 회의 리포트가 여기에 나타납니다.
              예측과 직무 쌍 얼라인먼트는 분석한 회의가 3회 이상 쌓여야 나타납니다.
            </p>
            <Link
              href="/meetings/new"
              style={{
                display: "inline-block",
                marginTop: "var(--space-12)",
                fontSize: "var(--text-meta)",
                color: "var(--color-accent-default)",
              }}
            >
              회의 시작하기
            </Link>
          </DashboardCard>
        </div>

        <div style={{ gridColumn: "1 / -1" }}>
          <WeeklyReportScheduleCard teamId={teamId} />
        </div>
      </div>
    );
  }

  // Half-width cards come in pairs, so no row ends in an empty cell (#1179).
  return (
    <div className="grid" style={gridStyle}>
      <QualityScoreCard dashboard={dashboard} />
      <AlignmentHeatmap cells={heatmap} />

      <div style={{ gridColumn: "1 / -1" }}>
        <GapDistributionBars distribution={dashboard.gap_distribution} titles={gapTitles} />
      </div>

      <ActionCompletionRate
        rate={dashboard.action_item_completion_rate}
        meetings={dashboard.action_completion_meeting_count}
        overdue={dashboard.overdue_action_items}
        asOf={dashboard.action_progress_as_of}
      />
      <PredictionCard predictions={predictions} />

      <div style={{ gridColumn: "1 / -1" }}>
        <MeetingReportsCard teamId={teamId} />
      </div>

      <div style={{ gridColumn: "1 / -1" }}>
        <WeeklyReportScheduleCard teamId={teamId} />
      </div>
    </div>
  );
}

const gridStyle = { gridTemplateColumns: "1fr 1fr", gap: "var(--space-12)" };

const metaStyle = { margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" };
