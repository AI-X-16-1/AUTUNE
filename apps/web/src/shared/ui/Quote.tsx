import type { ReactNode } from "react";

/** A quoted utterance: one paper block. No left bar, no decorative quote marks. */
export function Quote({ children }: { children: ReactNode }) {
  return (
    <blockquote
      className="rounded-[var(--radius)] px-4 py-3"
      style={{
        margin: 0,
        background: "var(--color-surface-paper)",
        color: "var(--color-ink-body)",
        fontSize: "var(--text-rowBody)",
        lineHeight: "var(--text-rowBody-leading)",
      }}
    >
      {children}
    </blockquote>
  );
}
