import { stepFor } from "./chartScale";
import { DashboardCard } from "./DashboardCard";
import type { DashboardRead, DashboardScoreEntry } from "../types";

const BAR_HEIGHT = 40;
const WEEKS_SHOWN = 8;

/**
 * The A–F grade + 8-week bar strip on S26.
 *
 * The big letter is `average_grade` (server-computed via the same
 * `_grade_for` thresholds the weekly report uses) so it and the "평균 N%"
 * text next to it describe the same population — never the most recent
 * meeting alone, which could show e.g. an "F" beside an 85% average.
 */
export function QualityScoreCard({ dashboard }: { dashboard: DashboardRead }) {
  const weeks = bucketByWeek(dashboard.recent_scores, WEEKS_SHOWN);

  return (
    <DashboardCard title="품질 점수">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div
            style={{
              fontSize: "var(--text-display)",
              fontWeight: "var(--text-display-weight)",
              color: "var(--color-ink-strong)",
              lineHeight: 1,
            }}
          >
            {dashboard.average_grade ?? "–"}
          </div>
          <div
            style={{
              marginTop: "var(--space-4)",
              fontSize: "var(--text-meta)",
              color: "var(--color-ink-muted)",
            }}
          >
            {dashboard.average_score != null
              ? `평균 ${Math.round(dashboard.average_score * 100)}% · 전체 ${dashboard.meeting_count}건`
              : "아직 분석된 회의가 없습니다"}
          </div>
        </div>
        {weeks.length > 0 ? (
          <div className="flex items-end gap-2" style={{ height: BAR_HEIGHT }}>
            {weeks.map((week) => (
              <div
                key={week.weekStart}
                role="img"
                aria-label={`${week.weekStart} 주 · 평균 ${Math.round(week.average * 100)}%`}
                title={`${week.weekStart} 주 · 평균 ${Math.round(week.average * 100)}%`}
                style={{
                  width: 11,
                  borderRadius: 1,
                  height: Math.max(4, Math.round(week.average * BAR_HEIGHT)),
                  background: stepFor(week.average),
                }}
              />
            ))}
          </div>
        ) : null}
      </div>
    </DashboardCard>
  );
}

interface WeekBucket {
  weekStart: string;
  average: number;
}

function bucketByWeek(scores: DashboardScoreEntry[], weeks: number): WeekBucket[] {
  const sums = new Map<string, { total: number; count: number }>();
  for (const score of scores) {
    const key = mondayOf(score.created_at);
    const entry = sums.get(key) ?? { total: 0, count: 0 };
    entry.total += score.value;
    entry.count += 1;
    sums.set(key, entry);
  }
  return [...sums.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .slice(-weeks)
    .map(([weekStart, { total, count }]) => ({ weekStart, average: total / count }));
}

/** The Monday (UTC) starting the week `iso` falls in, as `YYYY-MM-DD`. */
function mondayOf(iso: string): string {
  const date = new Date(iso);
  const daysSinceMonday = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - daysSinceMonday);
  return date.toISOString().slice(0, 10);
}
