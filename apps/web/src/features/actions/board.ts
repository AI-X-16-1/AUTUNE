import { COLUMNS, isCandidate } from "./types";
import type { ActionItemRead, ActionStatus } from "./types";

/**
 * Cards on their way to another column: the status each was dropped on, kept
 * until the server answers. A success replaces the item, a failure removes the
 * entry, and either way the card ends where the server says it is.
 */
export type Moves = Readonly<Record<string, ActionStatus>>;

/**
 * Said after a change that confirmed an item -- out of "확인 필요" -- by a drop
 * on the board or by the detail window's select. One sentence for both, so
 * the same act does not read differently by where it was done.
 */
export const CONFIRMED_NOTICE =
  "확정했습니다. 팀이 연결한 도구가 있으면 그쪽에도 반영됩니다.";

/**
 * What the detail window says after "끝내지 않고 닫기". There is no
 * confirmation before it (the user, 2026-10-09), and the status then reads
 * 완료 like finished work, so this is where the person learns what happened
 * and how to take it back.
 */
export const CLOSED_NOTICE =
  "끝내지 않고 닫았습니다. 상태를 다시 바꾸면 되돌릴 수 있습니다.";

/** Whether moving `item` to `target` is the move that confirms it. */
export function confirms(item: ActionItemRead, target: ActionStatus): boolean {
  return columnOf(item) === "needs_confirmation" && target !== "needs_confirmation";
}

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
/** Letters, not counting spaces. "다음 주 화요일까지 볼게요" is eleven. */
export const SHORT_SENTENCE = 16;

/**
 * Whether a card's sentence is the kind that says nothing by itself: one the
 * pipeline took from an utterance and nobody -- no model, no person --
 * rewrote, short enough to be an answer ("그럴게") or a pointer ("그건 제가
 * 볼게요"). Such a card is shown with the line said just before it
 * (`useCardContext`).
 *
 * Length is a rule of thumb, not a reading of the sentence: a short sentence
 * that is complete gets a hint it did not need, which costs a line; a long
 * one that still points at nothing gets none, and the detail window has it.
 */
export function pointsAtNothing(item: ActionItemRead): boolean {
  if (item.origin !== "model") return false;
  if (item.description_resolved) return false;
  if ((item.source_utterance_ids?.length ?? 0) === 0) return false;
  return [...item.description.replace(/\s/g, "")].length <= SHORT_SENTENCE;
}

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
