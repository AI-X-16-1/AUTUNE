import { DashboardCard } from "./DashboardCard";
import { GapTitleList, HoverPreview } from "./HoverPreview";

/**
 * `pipeline.base.PATTERN_TYPES` (module E, #203 — not yet merged into `main`
 * as of this component), in the fixed order shown here so the chart's row
 * order does not reshuffle as counts change week to week.
 *
 * Until #203 merges, `intel_gap_patterns.pattern_type` holds C's raw
 * free-text `category` instead, so a key outside this map falls back to its
 * own text via `PATTERN_LABELS[pattern] ?? pattern` and sorts after the known
 * patterns rather than before — see `rankOf` below.
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

/** Known patterns sort by `PATTERN_ORDER`; anything else sorts after them. */
function rankOf(pattern: string): number {
  const index = PATTERN_ORDER.indexOf(pattern);
  return index === -1 ? PATTERN_ORDER.length : index;
}

/** The 2px gap-type distribution bars on S26. */
export function GapDistributionBars({
  distribution,
  titles = {},
}: {
  distribution: Record<string, number>;
  /** Gap titles behind each pattern's count — `/gap-titles/{team_id}`, best-effort. */
  titles?: Record<string, string[]>;
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

  const ordered = present.sort(([a], [b]) => rankOf(a) - rankOf(b));
  const max = Math.max(...ordered.map(([, count]) => count));

  return (
    <DashboardCard title="갭 유형 분포">
      {ordered.map(([pattern, count]) => {
        const row = (
          <div className="flex items-center gap-2" style={{ margin: "7px 0" }}>
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
        );
        const patternTitles = titles[pattern];
        if (!patternTitles || patternTitles.length === 0) {
          return <div key={pattern}>{row}</div>;
        }
        return (
          <HoverPreview
            key={pattern}
            mockup={<GapTitleList titles={patternTitles} />}
            label="이 유형으로 분류된 갭"
          >
            {row}
          </HoverPreview>
        );
      })}
    </DashboardCard>
  );
}
