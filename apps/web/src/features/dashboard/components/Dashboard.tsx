"use client";

import { ActionCompletionRate } from "./ActionCompletionRate";
import { AlignmentHeatmap } from "./AlignmentHeatmap";
import { PlaceholderCard } from "./DashboardCard";
import { GapDistributionBars } from "./GapDistributionBars";
import { HoverPreview, InfluenceMapMockup, PredictionMockup } from "./HoverPreview";
import { QualityScoreCard } from "./QualityScoreCard";
import { useDashboard } from "../hooks/useDashboard";

/**
 * S26 — the team dashboard. No page route wires this up yet: `apps/web/src/app`
 * has no pages for any module, and `team_id` resolution needs the auth/team
 * context the whole app is still missing (#156, #189). A future page passes
 * `teamId` in once that lands.
 */
export function Dashboard({ teamId }: { teamId: string }) {
  const { dashboard, heatmap, gapTitles, loading, error } = useDashboard(teamId);

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

  return (
    <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: "var(--space-12)" }}>
      <QualityScoreCard dashboard={dashboard} />
      <AlignmentHeatmap cells={heatmap} />

      <div style={{ gridColumn: "1 / -1" }}>
        <GapDistributionBars distribution={dashboard.gap_distribution} titles={gapTitles} />
      </div>

      <div style={{ gridColumn: "1 / -1" }}>
        <ActionCompletionRate rate={dashboard.action_item_completion_rate} />
      </div>

      <HoverPreview mockup={<PredictionMockup />} side="left">
        <PlaceholderCard label="예측" />
      </HoverPreview>
      <HoverPreview mockup={<InfluenceMapMockup />}>
        <PlaceholderCard label="영향력 맵" />
      </HoverPreview>
    </div>
  );
}

const metaStyle = { margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" };
