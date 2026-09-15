"use client";

import { ActionCompletionRate } from "./ActionCompletionRate";
import { AlignmentHeatmap } from "./AlignmentHeatmap";
import { DashboardCard, PlaceholderCard } from "./DashboardCard";
import { GapDistributionBars } from "./GapDistributionBars";
import { QualityScoreCard } from "./QualityScoreCard";
import { useDashboard } from "../hooks/useDashboard";

/**
 * S26 — the team dashboard. No page route wires this up yet: `apps/web/src/app`
 * has no pages for any module, and `team_id` resolution needs the auth/team
 * context the whole app is still missing (#156, #189). A future page passes
 * `teamId` in once that lands.
 */
export function Dashboard({ teamId }: { teamId: string }) {
  const { dashboard, heatmap, loading, error } = useDashboard(teamId);

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

  if (dashboard.meeting_count === 0) {
    return (
      <DashboardCard title="품질 점수">
        <p style={metaStyle}>아직 분석된 회의가 없습니다.</p>
      </DashboardCard>
    );
  }

  return (
    <div className="flex flex-col" style={{ gap: "var(--space-12)" }}>
      <QualityScoreCard dashboard={dashboard} />

      <div className="grid" style={{ gridTemplateColumns: "1.1fr 1fr", gap: "var(--space-12)" }}>
        <AlignmentHeatmap cells={heatmap} />
        <GapDistributionBars distribution={dashboard.gap_distribution} />
      </div>

      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr 1fr", gap: "var(--space-12)" }}>
        <ActionCompletionRate rate={dashboard.action_item_completion_rate} />
        <PlaceholderCard label="예측" />
        <PlaceholderCard label="영향력 맵" />
      </div>
    </div>
  );
}

const metaStyle = { margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" };
