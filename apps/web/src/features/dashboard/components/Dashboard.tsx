"use client";

import { ActionCompletionRate } from "./ActionCompletionRate";
import { AlignmentHeatmap } from "./AlignmentHeatmap";
import { GapDistributionBars } from "./GapDistributionBars";
import { MeetingReportsCard } from "./MeetingReportsCard";
import { PredictionCard } from "./PredictionCard";
import { QualityScoreCard } from "./QualityScoreCard";
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

  return (
    <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: "var(--space-12)" }}>
      <QualityScoreCard dashboard={dashboard} />
      <AlignmentHeatmap cells={heatmap} />

      <div style={{ gridColumn: "1 / -1" }}>
        <GapDistributionBars distribution={dashboard.gap_distribution} titles={gapTitles} />
      </div>

      <div style={{ gridColumn: "1 / -1" }}>
        <ActionCompletionRate
          rate={dashboard.action_item_completion_rate}
          meetings={dashboard.action_completion_meeting_count}
          overdue={dashboard.overdue_action_items}
          asOf={dashboard.action_progress_as_of}
        />
      </div>

      <PredictionCard predictions={predictions} />

      <div style={{ gridColumn: "1 / -1" }}>
        <MeetingReportsCard teamId={teamId} />
      </div>
    </div>
  );
}

const metaStyle = { margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" };
