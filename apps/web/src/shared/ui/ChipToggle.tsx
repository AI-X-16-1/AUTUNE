/** A filter chip. Selected uses the accent selection background, nothing else. */
export function ChipToggle({
  selected = false,
  onClick,
  children,
}: {
  selected?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className="rounded-[var(--radius)] px-3 transition-colors"
      style={{
        height: "var(--control-h-compact)",
        fontSize: "var(--control-text-compact)",
        fontWeight: "var(--control-weight)",
        background: selected ? "var(--color-accent-selection)" : "var(--color-surface-sunken)",
        color: selected ? "var(--color-accent-hover)" : "var(--color-ink-body)",
      }}
    >
      {children}
    </button>
  );
}
