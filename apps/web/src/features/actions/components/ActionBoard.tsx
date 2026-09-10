"use client";

import { useMemo } from "react";

import { ActionCard } from "./ActionCard";
import { AddActionItem } from "./AddActionItem";
import { CandidateBand } from "./CandidateBand";
import { COLUMNS, COLUMN_LABELS, isCandidate } from "../types";
import type { ActionItemDraft } from "../api";
import type { ActionItem, ActionStatus } from "../types";

/**
 * S17. Four columns, left to right, in the order the work moves.
 *
 * The columns are the four `ActionStatus` values, so a status added to the
 * contract appears here as a type error rather than as a column nobody built.
 * `needs_confirmation` is Autune-only — no external issue exists for it yet.
 *
 * Candidates are not a fifth column. They are items the model was unsure about,
 * and ADR 0006 keeps them visible rather than dropping them because a missing
 * item costs the user far more than a wrong one. Putting them in a column would
 * claim they are work; the band says they are a question.
 *
 * `add` is one object rather than a `meetingId` and an `onAdd` beside each
 * other: a hand-added item is written to a meeting, so the two are only ever
 * useful together and passing one without the other should not typecheck. The
 * board renders read-only when it is absent — S15 lists items across meetings
 * and has no single meeting to add to.
 */
export function ActionBoard({
  items,
  selectedId,
  onSelect,
  add,
}: {
  items: ActionItem[];
  selectedId?: string;
  onSelect?: (id: string) => void;
  add?: { meetingId: string; onAdd: (draft: ActionItemDraft) => Promise<unknown> };
}) {
  const { candidates, byColumn } = useMemo(() => group(items), [items]);

  return (
    <div style={{ display: "grid", gap: "var(--space-page)" }}>
      {add !== undefined && <AddActionItem meetingId={add.meetingId} onAdd={add.onAdd} />}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        {COLUMNS.map((status) => (
          <section key={status} aria-label={COLUMN_LABELS[status]}>
            <header
              className="flex items-baseline gap-2 border-b border-[var(--color-hairline)] pb-2"
              style={{ fontSize: "var(--text-status)", fontWeight: "var(--text-status-weight)" }}
            >
              <span className="text-[var(--color-ink-strong)]">{COLUMN_LABELS[status]}</span>
              <span
                className="text-[var(--color-ink-muted)]"
                style={{ fontFamily: "var(--font-mono)" }}
              >
                {byColumn[status].length}
              </span>
            </header>

            <div className="mt-3 grid gap-2">
              {byColumn[status].map((item) => (
                <ActionCard
                  key={item.id}
                  item={item}
                  selected={item.id === selectedId}
                  onSelect={onSelect}
                />
              ))}
            </div>
          </section>
        ))}
      </div>

      <CandidateBand items={candidates} selectedId={selectedId} onSelect={onSelect} />
    </div>
  );
}

function group(items: ActionItem[]): {
  candidates: ActionItem[];
  byColumn: Record<ActionStatus, ActionItem[]>;
} {
  const byColumn = Object.fromEntries(COLUMNS.map((status) => [status, [] as ActionItem[]])) as
    Record<ActionStatus, ActionItem[]>;
  const candidates: ActionItem[] = [];

  for (const item of items) {
    // A candidate leaves the columns entirely rather than sitting in
    // "needs confirmation" alongside items the model is sure about. The column
    // means "no external issue yet"; the band means "we are not sure this is an
    // item", and merging the two loses the difference the user needs.
    if (isCandidate(item)) {
      candidates.push(item);
      continue;
    }
    byColumn[item.status ?? "needs_confirmation"].push(item);
  }

  return { candidates, byColumn };
}
