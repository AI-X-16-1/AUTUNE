import { DashboardCard } from "./DashboardCard";

/**
 * `pipeline.base.PATTERN_TYPES` (module E), in the fixed order shown here so
 * the chart's row order does not reshuffle as counts change week to week.
 */
const PATTERN_LABELS: Record<string, string> = {
  schedule: "일정",
  ownership: "담당자",
  budget: "예산",
  stakeholder: "이해관계자",
  risk: "리스크",
  scope: "범위",
  other: "기타",
};
const PATTERN_ORDER = Object.keys(PATTERN_LABELS);

/** The 2px gap-type distribution bars on S26. */
export function GapDistributionBars({
  distribution,
}: {
  distribution: Record<string, number>;
}) {
  const present = Object.entries(distribution).filter(([, count]) => count > 0);
  if (present.length === 0) {
    return (
      <DashboardCard title="갭 유형 분포">
        <p style={{ margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" }}>
          아직 분류된 갭이 없습니다.
        </p>
      </DashboardCard>
    );
  }

  const ordered = present.sort(
    ([a], [b]) => PATTERN_ORDER.indexOf(a) - PATTERN_ORDER.indexOf(b),
  );
  const max = Math.max(...ordered.map(([, count]) => count));

  return (
    <DashboardCard title="갭 유형 분포">
      {ordered.map(([pattern, count]) => (
        <div key={pattern} className="flex items-center gap-2" style={{ margin: "7px 0" }}>
          <span
            style={{
              width: 80,
              fontSize: "var(--text-rowLabel)",
              color: "var(--color-ink-body)",
            }}
          >
            {PATTERN_LABELS[pattern] ?? pattern}
          </span>
          <span
            className="flex-1"
            style={{ height: "var(--bar-thickness)", background: "var(--color-surface-sunken)" }}
          >
            <span
              className="block"
              style={{
                height: "100%",
                width: `${Math.round((count / max) * 100)}%`,
                background: "var(--color-chart-step4)",
              }}
            />
          </span>
          <span
            style={{
              width: 20,
              textAlign: "right",
              fontFamily: "var(--font-mono)",
              fontSize: "var(--text-dataSmall)",
              color: "var(--color-ink-muted)",
            }}
          >
            {count}
          </span>
        </div>
      ))}
    </DashboardCard>
  );
}
