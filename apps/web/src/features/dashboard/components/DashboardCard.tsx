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
