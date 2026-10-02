import { StatusDot } from "@/shared/ui";

import { COVERAGE_LABELS } from "../types";
import type { TemplateChecklistItem } from "../types";

/**
 * The 충족 tab: the checklist items the meeting did cover.
 *
 * **There is no card to draw, only the item.** A covered item raises no gap,
 * so it has no score, no question and no quote — `gap_gaps` holds only
 * `partial` and `missing`, and the server reads the absence of a row back as
 * `covered` (see `TemplateRail`). The row names the item and nothing it cannot
 * back.
 *
 * In the order the template lists them, the order the rail reads in, so an
 * item is found in the same place on both sides. Not grouped by `category`:
 * that is a key (`measurement`), not copy a reader sees.
 */
export function CoveredList({ items }: { items: readonly TemplateChecklistItem[] }) {
  if (items.length === 0) {
    return (
      <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
        충족으로 판정된 템플릿 항목이 없습니다.
      </p>
    );
  }

  return (
    <div className="flex flex-col" style={{ gap: "var(--space-16)" }}>
      <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
        표시 {items.length}건 · 충족한 항목은 갭을 만들지 않아 점수와 질문이 없습니다.
      </p>
      <ul className="border-t border-[var(--color-hairline)]">
        {items.map((item) => (
          <li
            key={item.key}
            className="grid items-center border-b border-[var(--color-hairline)]"
            style={{
              gridTemplateColumns: "auto 1fr auto",
              gap: "var(--space-8)",
              padding: "10px 0",
            }}
          >
            <StatusDot variant="confirmed" />
            <span
              className="min-w-0 truncate text-[var(--color-ink-strong)]"
              style={{
                fontSize: "var(--text-rowLabel)",
                fontWeight: "var(--text-rowLabel-weight)",
              }}
            >
              {item.item}
            </span>
            <span
              className="text-[var(--color-ink-muted)]"
              style={{ fontSize: "var(--text-metaSmall)" }}
            >
              {COVERAGE_LABELS.covered}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
