import { DashboardCard } from "./DashboardCard";

/**
 * The action-item completion rate stat on S26: done over confirmed across the
 * team's meetings held in the last four weeks, from B's latest counts (#605).
 * Team totals only. The window is the server's (`ACTION_COMPLETION_WINDOW`).
 *
 * `asOf` is `null` when those counts are missing or stale -- unknown, not 0%.
 * B sends none for a team with no meeting in 13 weeks, so such a team reads as
 * not received. With `asOf` set and no `rate`, either nothing is confirmed in
 * the window or it holds fewer than three meetings (`meetings`): the server
 * withholds a total that would be one or two meetings' counts (#800 review).
 * `overdue` counts every kept meeting, and is `null` under the same floor.
 */
export function ActionCompletionRate({
  rate,
  meetings,
  overdue,
  asOf,
}: {
  rate: number | null;
  meetings: number | null;
  overdue: number | null;
  asOf: string | null;
}) {
  if (asOf == null) {
    return (
      <DashboardCard title="액션 아이템 완료율">
        <p style={metaStyle}>완료 현황을 아직 받지 못했습니다.</p>
      </DashboardCard>
    );
  }
  return (
    <DashboardCard title="액션 아이템 완료율">
      {rate != null ? (
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
        </>
      ) : (
        <p style={metaStyle}>
          {meetings != null && meetings > 0 && meetings < MIN_MEETINGS
            ? "최근 4주 회의가 3건 미만이라 완료율을 표시하지 않습니다."
            : "최근 4주 회의에서 확정된 액션 아이템이 없습니다."}
        </p>
      )}
      <p style={{ ...metaStyle, marginTop: "var(--space-8)" }}>
        {overdue != null && (
          <>
            <span style={overdue ? { color: "var(--color-signal-critical)" } : undefined}>
              기한 지난 항목 {overdue}건
            </span>
            {" (보관 중인 회의 전체) · "}
          </>
        )}
        {rate != null && "완료율은 최근 4주 회의 · "}
        {`${formatTime(asOf)} 기준`}
      </p>
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

/**
 * The server's `ACTION_PROGRESS_MIN_MEETINGS`. B lists only meetings with a
 * confirmed item, so a window with three or more always has a rate; the bound
 * is still checked here so the copy says what it means (#812 review).
 */
const MIN_MEETINGS = 3;

const metaStyle = { margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" };
