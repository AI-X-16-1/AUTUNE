/**
 * Why the button beside it cannot be pressed, in words on the screen (#1177).
 *
 * A `title` alone is not enough: most browsers show no tooltip on a disabled
 * button, and a touch screen shows none at all. The button points here with
 * `aria-describedby`, so a screen reader reads the reason with it.
 *
 * Kept in the feature until the shared `Button` has a way to carry a reason
 * (#1180).
 */
export function DisabledReason({ id, children }: { id: string; children: string }) {
  return (
    <span
      id={id}
      className="whitespace-nowrap text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-metaSmall)" }}
    >
      {children}
    </span>
  );
}
