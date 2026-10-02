import { StatusDot, type StatusVariant } from "@/shared/ui";

import { KIND_LABELS, type UtteranceKind } from "../types";

/**
 * Each kind's mark, as S13 draws it. A decision is a hollow ring, as decisions
 * are everywhere (`StatusDot`); a commitment is a filled ink dot; an open
 * question is the accent's "in progress"; a concern is red; and an ambiguous
 * line is ochre, because ochre means "needs a person" everywhere else in the
 * product and it is the only kind that asks somebody to do something.
 */
const MARK: Record<UtteranceKind, { variant: StatusVariant; hollow?: boolean }> = {
  decision: { variant: "confirmed", hollow: true },
  commitment: { variant: "confirmed" },
  open_question: { variant: "progress" },
  concern: { variant: "critical" },
  ambiguous: { variant: "attention" },
};

/** The dot alone, shared by a row's tag and the rail's tally. */
export function KindMark({ kind }: { kind: UtteranceKind }) {
  const { variant, hollow } = MARK[kind];
  return <StatusDot variant={variant} hollow={hollow} />;
}

/**
 * What module B made of an utterance. Five kinds, and most rows have none.
 *
 * A dot and text, not a filled badge: `ui-spec.md` allows no pills, and a
 * coloured badge on every classified row would turn the transcript into a
 * colour chart. The label stays in ink for every kind; the dot carries the
 * kind's colour.
 *
 * The labels come from `types.ts`, shared with the rail's tally, so a row and
 * the rail cannot end up calling the same kind two different things.
 */
export function KindTag({ kind }: { kind: UtteranceKind }) {
  return (
    <span
      className="flex items-center"
      style={{
        gap: 7,
        fontSize: "var(--text-status)",
        fontWeight: "var(--text-status-weight)",
        lineHeight: "var(--text-status-leading)",
        color: "var(--color-ink-strong)",
        whiteSpace: "nowrap",
      }}
    >
      <KindMark kind={kind} />
      {KIND_LABELS[kind].tag}
    </span>
  );
}
