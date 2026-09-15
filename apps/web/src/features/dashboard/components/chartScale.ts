/**
 * The 5-step achromatic ramp every S26 chart fills with — `color.*.chart` in
 * `docs/design/design-tokens.json`. Signal colours never fill a chart; they
 * appear on numeric text only, so nothing here reaches for red or ochre.
 */
export const CHART_STEPS = [
  "var(--color-chart-step1)",
  "var(--color-chart-step2)",
  "var(--color-chart-step3)",
  "var(--color-chart-step4)",
  "var(--color-chart-step5)",
] as const;

/** Maps a 0–1 value onto the ramp, low to high. */
export function stepFor(value: number): string {
  const clamped = Math.min(CHART_STEPS.length - 1, Math.max(0, Math.floor(value * CHART_STEPS.length)));
  return CHART_STEPS[clamped] ?? CHART_STEPS[0];
}
