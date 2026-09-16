import { DashboardCard } from "./DashboardCard";

/** The action-item completion rate stat on S26. */
export function ActionCompletionRate({ rate }: { rate: number | null }) {
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
        <p style={{ margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" }}>
          데이터 없음
        </p>
      )}
    </DashboardCard>
  );
}
