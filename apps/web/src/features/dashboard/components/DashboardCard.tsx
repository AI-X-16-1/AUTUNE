import type { ReactNode } from "react";

/** The one card shell every S26 widget shares — solid border, uppercase label. */
export function DashboardCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div
      style={{
        background: "var(--color-surface-panel)",
        border: "1px solid var(--color-hairline)",
        borderRadius: "var(--radius)",
        padding: "var(--space-card)",
      }}
    >
      <h4
        className="uppercase"
        style={{
          margin: 0,
          marginBottom: "var(--space-12)",
          fontSize: "var(--text-label)",
          fontWeight: "var(--text-label-weight)",
          letterSpacing: "0.02em",
          color: "var(--color-ink-muted)",
        }}
      >
        {title}
      </h4>
      {children}
    </div>
  );
}

/** The Phase 2 placeholder — dashed border, no data, per #28/#27 (not decided yet). */
export function PlaceholderCard({ label }: { label: string }) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-2"
      style={{
        background: "var(--color-surface-panel)",
        border: "1px dashed var(--color-surface-sunken)",
        borderRadius: "var(--radius)",
        padding: "var(--space-card)",
        minHeight: 96,
      }}
    >
      <span
        style={{
          fontSize: "var(--text-metaSmall)",
          color: "var(--color-ink-muted)",
          border: "1px solid var(--color-surface-sunken)",
          borderRadius: "var(--radius)",
          padding: "1px 6px",
        }}
      >
        PHASE 2
      </span>
      <p style={{ margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" }}>
        {label}
      </p>
    </div>
  );
}
