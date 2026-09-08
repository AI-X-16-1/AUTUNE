/**
 * Priority-3 status: a coloured word, a monospace value, and a 2px bar.
 * Signal colours appear on the figure — never as a chart or badge fill.
 */
export type ScoreLevel = "high" | "medium" | "low";

const LEVEL: Record<ScoreLevel, { label: string; color: string }> = {
  high: { label: "높음", color: "var(--color-signal-critical)" },
  medium: { label: "중간", color: "var(--color-signal-attention)" },
  low: { label: "낮음", color: "var(--color-ink-muted)" },
};

export function ScoreLabel({ level, score }: { level: ScoreLevel; score: number }) {
  const { label, color } = LEVEL[level];
  return (
    <span className="inline-flex items-center gap-2">
      <span style={{ color, fontSize: "var(--text-status)", fontWeight: 500 }}>{label}</span>
      <span
        style={{
          color,
          fontFamily: "var(--font-mono)",
          fontSize: "var(--text-data)",
          fontWeight: "var(--text-data-weight)",
        }}
      >
        {score.toFixed(2)}
      </span>
      <span
        aria-hidden
        className="inline-block w-16 rounded-full"
        style={{ height: "var(--bar-thickness)", background: "var(--color-surface-sunken)" }}
      >
        <span
          className="block h-full rounded-full"
          style={{ width: `${Math.round(score * 100)}%`, background: color }}
        />
      </span>
    </span>
  );
}
