import type { UtteranceKind } from "../types";

/**
 * What module B made of an utterance. Five kinds, and most rows have none.
 *
 * Text, not a filled badge: `ui-spec.md` allows no pills, and a tag on every
 * classified row would turn the transcript into a colour chart. The one that
 * carries a signal colour is `ambiguous` — it is the only kind that asks
 * somebody to do something, and it is ochre because ochre means "needs a
 * person" everywhere else in the product.
 */
const LABELS: Record<UtteranceKind, string> = {
  commitment: "약속",
  decision: "결정",
  open_question: "질문",
  concern: "우려",
  ambiguous: "확인 필요",
};

export function KindTag({ kind }: { kind: UtteranceKind }) {
  return (
    <span
      style={{
        fontSize: "var(--text-status)",
        fontWeight: "var(--text-status-weight)",
        color:
          kind === "ambiguous"
            ? "var(--color-signal-attention)"
            : "var(--color-ink-muted)",
        whiteSpace: "nowrap",
      }}
    >
      {LABELS[kind]}
    </span>
  );
}
