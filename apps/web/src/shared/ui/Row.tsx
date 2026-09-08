import type { ReactNode } from "react";

/**
 * The basic unit of every list: dot -> title -> meta -> actions.
 * Actions sit right, stay compact, and include at most one primary.
 * A hairline separates rows; no card inside a card.
 */
export function Row({
  dot,
  title,
  meta,
  actions,
  selected = false,
}: {
  dot?: ReactNode;
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  selected?: boolean;
}) {
  return (
    <div
      className="flex items-center gap-3 border-b border-[var(--color-hairline)]"
      style={{
        paddingBlock: "var(--space-row)",
        background: selected ? "var(--color-accent-selection)" : undefined,
      }}
    >
      {dot}
      <div className="min-w-0 flex-1">
        <div
          className="truncate text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-rowTitle)", fontWeight: "var(--text-rowTitle-weight)" }}
        >
          {title}
        </div>
        {meta ? (
          <div
            className="truncate text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-metaSmall)" }}
          >
            {meta}
          </div>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}
