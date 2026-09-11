import { KIND_LABELS, type UtteranceKind } from "../types";

/**
 * What module B made of an utterance. Five kinds, and most rows have none.
 *
 * Text, not a filled badge: `ui-spec.md` allows no pills, and a tag on every
 * classified row would turn the transcript into a colour chart. The one that
 * carries a signal colour is `ambiguous` — it is the only kind that asks
 * somebody to do something, and it is ochre because ochre means "needs a
 * person" everywhere else in the product.
 *
 * The labels come from `types.ts`, shared with the rail's tally, so a row and
 * the rail cannot end up calling the same kind two different things.
 */
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
      {KIND_LABELS[kind]}
    </span>
  );
}
