"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { ActionCard } from "./ActionCard";
import { AddActionItem } from "./AddActionItem";
import { CandidateBand } from "./CandidateBand";
import { canDrop, columnOf, groupForBoard } from "../board";
import { COLUMNS, COLUMN_LABELS } from "../types";
import type { ActionItemDraft } from "../api";
import type { Moves } from "../board";
import type { ActionItemRead, ActionStatus } from "../types";

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
 *
 * **`onMove` makes a card draggable to another column.** A drop is the same
 * change the detail window's status select makes, sent the same way, and so it
 * carries the same weight: out of "확인 필요" is a confirmation, after which
 * the item is copied to the tools the team connected (#246); back into it
 * takes those copies' text with it (#622). Decided with the user, 2026-10-02:
 * a drop applies at once, as the select does, and a candidate cannot be
 * dragged.
 *
 * The select stays the way to do it without a pointer: the browser's own drag
 * and drop has no keyboard path and does not fire on most touch screens.
 *
 * **A drop that confirmed an item says so afterwards** (#712): one line, not a
 * question. The drop has already applied, as decided; what the line adds is
 * that a person who meant only to tidy the board learns that this move was
 * the one that sends the item on. It offers no undo -- whether one can be
 * honoured once a copy has left is not settled.
 */
export function ActionBoard({
  items,
  selectedId,
  onSelect,
  add,
  showMeeting = false,
  onMove,
}: {
  items: ActionItemRead[];
  selectedId?: string;
  onSelect?: (id: string) => void;
  add?: { meetingId: string; onAdd: (draft: ActionItemDraft) => Promise<unknown> };
  /** Each card names its meeting -- the board across meetings. */
  showMeeting?: boolean;
  /** Set an item's status. Rejects when the server refused, leaving it where it was. */
  onMove?: (id: string, status: ActionStatus) => Promise<unknown>;
}) {
  // Drawn in the column it was dropped on while the request is in flight, so
  // the card does not spring back and then jump; see `Moves`.
  const [moves, setMoves] = useState<Moves>({});
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [over, setOver] = useState<ActionStatus | null>(null);
  // The select in the detail window says why a change did not hold; a card
  // that only slid back would say nothing (review of #292).
  const [failure, setFailure] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // The board's own state changes a tick after `dragstart`, not inside it: a
  // re-render of the card while the browser is still starting the drag
  // cancels the drag in some versions of Chrome (review of #710).
  const starting = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(starting.current), []);

  const { candidates, byColumn } = useMemo(() => groupForBoard(items, moves), [items, moves]);
  const dragged = draggedId === null ? undefined : items.find((item) => item.id === draggedId);

  const drop = async (target: ActionStatus) => {
    window.clearTimeout(starting.current);
    setDraggedId(null);
    setOver(null);
    if (onMove === undefined || !canDrop(dragged, target, moves)) return;
    const id = dragged.id;
    const confirms = columnOf(dragged) === "needs_confirmation";
    setFailure(null);
    setNotice(null);
    setMoves((current) => ({ ...current, [id]: target }));
    try {
      await onMove(id, target);
      if (confirms) {
        setNotice("확정했습니다. 팀이 연결한 도구가 있으면 그쪽에도 반영됩니다.");
      }
    } catch {
      setFailure("상태를 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setMoves((current) =>
        Object.fromEntries(Object.entries(current).filter(([moving]) => moving !== id)),
      );
    }
  };

  return (
    <div style={{ display: "grid", gap: "var(--space-page)" }}>
      {add !== undefined && <AddActionItem meetingId={add.meetingId} onAdd={add.onAdd} />}

      {failure !== null ? (
        <p
          role="alert"
          className="text-[var(--color-signal-critical)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {failure}
        </p>
      ) : null}
      {notice !== null ? (
        <p
          role="status"
          className="text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {notice}
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        {COLUMNS.map((status) => {
          // Only a column this card can go to takes the drop: its own column,
          // and anything dragged in from outside the board, fall through to
          // the browser's "not allowed".
          const accepts = onMove !== undefined && canDrop(dragged, status, moves);
          return (
            <section
              key={status}
              aria-label={COLUMN_LABELS[status]}
              onDragOver={
                accepts
                  ? (event) => {
                      event.preventDefault();
                      event.dataTransfer.dropEffect = "move";
                    }
                  : undefined
              }
              onDragEnter={accepts ? () => setOver(status) : undefined}
              onDragLeave={(event) => {
                // Entering a card inside the column fires a leave on the column.
                if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
                setOver((current) => (current === status ? null : current));
              }}
              onDrop={
                accepts
                  ? (event) => {
                      event.preventDefault();
                      void drop(status);
                    }
                  : undefined
              }
              style={{
                borderRadius: "var(--radius)",
                background: accepts && over === status ? "var(--color-surface-sunken)" : undefined,
              }}
            >
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

              {/* A floor, so an empty column is still somewhere to drop. */}
              <div className="mt-3 grid content-start gap-2" style={{ minHeight: "var(--space-48)" }}>
                {byColumn[status].map((item) => (
                  <ActionCard
                    key={item.id}
                    item={item}
                    selected={item.id === selectedId}
                    onSelect={onSelect}
                    showMeeting={showMeeting}
                    drag={
                      onMove === undefined
                        ? undefined
                        : {
                            moving: item.id in moves,
                            onStart: () => {
                              window.clearTimeout(starting.current);
                              starting.current = window.setTimeout(() => {
                                setFailure(null);
                                setNotice(null);
                                setDraggedId(item.id);
                              }, 0);
                            },
                            onEnd: () => {
                              window.clearTimeout(starting.current);
                              setDraggedId(null);
                              setOver(null);
                            },
                          }
                    }
                  />
                ))}
              </div>
            </section>
          );
        })}
      </div>

      <CandidateBand
        items={candidates}
        selectedId={selectedId}
        onSelect={onSelect}
        showMeeting={showMeeting}
      />
    </div>
  );
}
