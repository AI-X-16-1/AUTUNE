import { StatusDot } from "@/shared/ui";

import { isCandidate } from "../types";
import type { ActionItemRead } from "../types";

/**
 * One card on the action board (S17).
 *
 * Reading order is fixed: title → reason → assignee and due date → issue key.
 * The reason sits second because ADR 0006 makes every item a draft the user
 * finishes, and the first thing they need is why the model thinks this is an
 * item at all.
 *
 * A broken integration link is red text, never a red fill — red belongs to
 * elapsing time and failure (ui-spec section 0).
 */
export function ActionCard({
  item,
  selected = false,
  onSelect,
}: {
  item: ActionItemRead;
  selected?: boolean;
  onSelect?: (id: string) => void;
}) {
  const overdue = isOverdue(item.due_date);

  return (
    <button
      type="button"
      onClick={() => onSelect?.(item.id)}
      aria-current={selected}
      className="w-full border text-left"
      style={{
        background: "var(--color-surface-paper)",
        borderRadius: "var(--radius)",
        padding: "var(--space-card)",
        borderWidth: selected ? 1.5 : 1,
        borderColor: selected ? "var(--color-accent-default)" : "var(--color-hairline)",
      }}
    >
      <div
        className="text-[var(--color-ink-strong)]"
        style={{ fontSize: "var(--text-rowTitle)", fontWeight: "var(--text-rowTitle-weight)" }}
      >
        {item.description}
      </div>

      <div
        className="mt-1 text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        {reasonFor(item)}
      </div>

      <div className="mt-2 flex items-center gap-2" style={{ fontSize: "var(--text-metaSmall)" }}>
        <span className="text-[var(--color-ink-body)]">
          {item.assignee_name ?? item.assignee_label ?? "담당 미지정"}
        </span>
        {item.due_date ? (
          <span
            style={{
              fontFamily: "var(--font-mono)",
              color: overdue ? "var(--color-signal-critical)" : "var(--color-ink-muted)",
            }}
          >
            {item.due_date}
          </span>
        ) : null}
      </div>

      {item.sync_refs?.length ? (
        <div
          className="mt-2 flex items-center gap-2 text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {item.sync_refs.map((ref) => (
            <span key={`${ref.system}-${ref.url}`} className="flex items-center gap-1">
              <StatusDot variant={ref.url ? "confirmed" : "critical"} />
              <span style={{ fontFamily: "var(--font-mono)" }}>
                {ref.external_id ?? ref.system}
              </span>
            </span>
          ))}
        </div>
      ) : null}
    </button>
  );
}

/**
 * Why this card exists, in the user's terms.
 *
 * A candidate says so first. The point of showing a low-confidence item at all
 * is that the user can judge it, and a card that looks identical to a confident
 * one asks them to trust something the model did not.
 *
 * "직접 추가" is read from `origin`, not inferred from an empty source list. A
 * model item whose utterances were deleted with the transcript also has none,
 * and calling it hand-added would print the distinction edit cost is measured
 * on the wrong way round.
 */
function reasonFor(item: ActionItemRead): string {
  if (item.origin === "user") return "직접 추가";
  const sources = item.source_utterance_ids?.length ?? 0;
  return isCandidate(item) ? `후보 · 근거 발화 ${sources}건` : `근거 발화 ${sources}건`;
}

function isOverdue(dueDate: string | null | undefined): boolean {
  if (!dueDate) return false;
  const today = new Date().toISOString().slice(0, 10);
  return dueDate < today;
}
