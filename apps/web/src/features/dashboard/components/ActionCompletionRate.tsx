import { DashboardCard } from "./DashboardCard";

/**
 * The action-item completion rate stat on S26: done over confirmed across the
 * team's meetings held in the last four weeks, from B's latest counts (#605).
 * Team totals only. The window is the server's (`ACTION_COMPLETION_WINDOW`).
 *
 * `asOf` is `null` when those counts are missing or stale -- unknown, not 0%.
 * B sends none for a team with no meeting in 13 weeks, so such a team reads as
 * not received.
 * With `asOf` set and no `rate`, nothing is confirmed yet.
 */
export function ActionCompletionRate({
  rate,
  overdue,
  asOf,
}: {
  rate: number | null;
  overdue: number | null;
  asOf: string | null;
}) {
  return (
    <DashboardCard title="액션 아이템 완료율">
      {asOf == null ? (
        <p style={metaStyle}>완료 현황을 아직 받지 못했습니다.</p>
      ) : rate == null ? (
        <p style={metaStyle}>최근 4주 회의에서 확정된 액션 아이템이 없습니다.</p>
      ) : (
        <>
          <div
            style={{
              fontSize: "var(--text-title)",
              fontWeight: "var(--text-title-weight)",
              color: "var(--color-ink-strong)",
              fontFamily: "var(--font-mono)",
            }}
          >
            {Math.round(rate * 100)}%
          </div>
          <div
            style={{
              marginTop: "var(--space-8)",
              height: "var(--bar-thickness)",
              borderRadius: 3,
              background: "var(--color-surface-sunken)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                height: "100%",
                width: `${Math.round(rate * 100)}%`,
                background: "var(--color-chart-step4)",
              }}
            />
          </div>
          <p style={{ ...metaStyle, marginTop: "var(--space-8)" }}>
            <span style={overdue ? { color: "var(--color-signal-critical)" } : undefined}>
              기한 지난 항목 {overdue ?? 0}건
            </span>
            {` · 최근 4주 회의 · ${formatTime(asOf)} 기준`}
          </p>
        </>
      )}
    </DashboardCard>
  );
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const metaStyle = { margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" };
