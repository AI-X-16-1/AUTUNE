import { COLUMNS, isCandidate } from "./types";
import type { ActionItemRead, ActionStatus } from "./types";

/**
 * Cards on their way to another column: the status each was dropped on, kept
 * until the server answers. A success replaces the item, a failure removes the
 * entry, and either way the card ends where the server says it is.
 */
export type Moves = Readonly<Record<string, ActionStatus>>;

/** The column an item is drawn in. */
export function columnOf(
  item: ActionItemRead,
  moves: Moves = {},
): ActionStatus {
  return moves[item.id] ?? item.status ?? "needs_confirmation";
}

/**
 * Whether dropping `item` on `target` is a change worth sending.
 *
 * Not a candidate: the band asks whether it is an item at all, which the detail
 * window answers, and a drag would answer it by accident. Not a card already on
 * its way somewhere: a second drop would race the first, the way a second pick
 * in the detail window's select would (review of #292). Not its own column.
 */
export function canDrop(
  item: ActionItemRead | undefined,
  target: ActionStatus,
  moves: Moves = {},
): item is ActionItemRead {
  if (item === undefined || isCandidate(item)) return false;
  if (item.id in moves) return false;
  return columnOf(item) !== target;
}

export function groupForBoard(
  items: ActionItemRead[],
  moves: Moves = {},
): {
  candidates: ActionItemRead[];
  byColumn: Record<ActionStatus, ActionItemRead[]>;
} {
  const byColumn = Object.fromEntries(
    COLUMNS.map((status) => [status, [] as ActionItemRead[]]),
  ) as Record<ActionStatus, ActionItemRead[]>;
  const candidates: ActionItemRead[] = [];

  for (const item of items) {
    // A candidate leaves the columns entirely rather than sitting in
    // "needs confirmation" alongside items the model is sure about. The column
    // means "no external issue yet"; the band means "we are not sure this is an
    // item", and merging the two loses the difference the user needs.
    if (isCandidate(item)) {
      candidates.push(item);
      continue;
    }
    byColumn[columnOf(item, moves)].push(item);
  }

  // ADR 0007: an item whose assignee left the team goes to the top of its
  // column instead of sitting invisibly unowned. Stable sort, so the server's
  // order holds within each group.
  for (const status of COLUMNS) {
    byColumn[status].sort(
      (a, b) =>
        Number(b.needs_reassignment ?? false) -
        Number(a.needs_reassignment ?? false),
    );
  }

  return { candidates, byColumn };
}
