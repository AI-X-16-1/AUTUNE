/**
 * The one way to show state: a 6px dot plus sentence-case text.
 * No filled badges, no pills, no left colour bars.
 *
 * A decision is a hollow ring; so is anything pending.
 * See docs/design/ui-spec.md section 2.
 */
export type StatusVariant = "confirmed" | "progress" | "attention" | "critical" | "idle";

const COLOR: Record<StatusVariant, string> = {
  confirmed: "var(--color-signal-confirmed)",
  progress: "var(--color-signal-progress)",
  attention: "var(--color-signal-attention)",
  critical: "var(--color-signal-critical)",
  idle: "var(--color-signal-idle)",
};

export function StatusDot({
  variant = "idle",
  hollow = false,
  className = "",
}: {
  variant?: StatusVariant;
  /** Decisions and not-yet-started items are rings, not filled dots. */
  hollow?: boolean;
  className?: string;
}) {
  const size = hollow ? 7 : 6;
  return (
    <span
      aria-hidden
      className={`inline-block shrink-0 rounded-full ${className}`}
      style={{
        width: size,
        height: size,
        background: hollow ? "transparent" : COLOR[variant],
        border: hollow ? `1.5px solid ${COLOR[variant]}` : undefined,
      }}
    />
  );
}
