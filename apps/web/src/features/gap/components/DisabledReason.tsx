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
      // Capped and allowed to wrap below `sm`: at 360px a one-line reason
      // beside the button would push the top bar past the screen. The floor
      // keeps a squeezed row from breaking it a letter per line.
      className="min-w-[6rem] max-w-[9rem] text-right leading-tight text-[var(--color-ink-muted)] sm:max-w-none sm:whitespace-nowrap"
      style={{ fontSize: "var(--text-metaSmall)" }}
    >
      {children}
    </span>
  );
}
