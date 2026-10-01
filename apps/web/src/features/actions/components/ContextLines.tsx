import { MaskedText } from "@/shared/ui";

import type { SourceUtterance } from "../types";

/**
 * The lines said just before a quotation, muted and plain, so a sentence with
 * nothing to point at ("다음 주 화요일까지 볼게요") reads with what it is about.
 *
 * Not a quotation of the item's sources — those keep the `Quote` treatment
 * beneath — and it says so, so nobody reads a neighbouring line as the evidence.
 */
export function ContextLines({
  lines,
  label = "앞선 발화 (맥락)",
}: {
  lines: SourceUtterance[];
  label?: string;
}) {
  if (lines.length === 0) return null;
  return (
    <div className="grid gap-0.5" role="group" aria-label={label}>
      <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
        {label}
      </p>
      {lines.map((line) => (
        <p
          key={line.id}
          className="text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)", lineHeight: "var(--text-body-leading)" }}
        >
          <MaskedText>{line.text}</MaskedText>
        </p>
      ))}
    </div>
  );
}
