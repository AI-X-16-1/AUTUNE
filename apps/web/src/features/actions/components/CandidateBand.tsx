import { ActionCard } from "./ActionCard";
import type { ActionItem } from "../types";

/**
 * Items the model was not confident enough to assert (ADR 0006).
 *
 * They are shown rather than dropped because recall is ranked above precision:
 * a wrong item costs a click, a missing one costs re-reading a 45-minute
 * meeting. Dropping them would hide exactly the items the user cannot recover
 * any other way.
 *
 * Below the board, not beside it. These are not a stage of the work, and a
 * fifth column would read as one.
 */
export function CandidateBand({
  items,
  selectedId,
  onSelect,
}: {
  items: ActionItem[];
  selectedId?: string;
  onSelect?: (id: string) => void;
}) {
  if (items.length === 0) return null;

  return (
    <section
      aria-label="후보"
      className="border-t border-[var(--color-hairline)]"
      style={{ paddingTop: "var(--space-page)" }}
    >
      <header className="flex items-baseline gap-2">
        <h2
          className="text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-status)", fontWeight: "var(--text-status-weight)" }}
        >
          후보
        </h2>
        <span
          className="text-[var(--color-ink-muted)]"
          style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-metaSmall)" }}
        >
          {items.length}
        </span>
      </header>

      <p
        className="mt-1 text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        확신이 낮아 액션 아이템으로 확정하지 않은 발화입니다. 맞으면 담당과 기한을 채워 보드로
        올리고, 아니면 지우면 됩니다.
      </p>

      <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-4">
        {items.map((item) => (
          <ActionCard
            key={item.id}
            item={item}
            selected={item.id === selectedId}
            onSelect={onSelect}
          />
        ))}
      </div>
    </section>
  );
}
