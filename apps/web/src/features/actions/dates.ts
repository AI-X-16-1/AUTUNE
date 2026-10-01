import type { ActionItemRead } from "./types";

/**
 * Today where the viewer is, as `YYYY-MM-DD` -- the form `due_date` comes in.
 *
 * Not `new Date().toISOString().slice(0, 10)`: that is today in UTC, which in
 * Korea is still yesterday until 09:00, so an item due yesterday read as not
 * yet overdue every morning (review of #540).
 */
export function localToday(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

/**
 * Past its due date and not done. One rule for every place that marks an item
 * overdue -- the card's red date and the team's overdue list -- so the two
 * never disagree about the same item. A done item is finished work, however
 * late; an undated one cannot be late.
 */
export function isOverdue(
  item: Pick<ActionItemRead, "due_date" | "status">,
  today: string = localToday(),
): boolean {
  if (!item.due_date || item.status === "done") return false;
  return item.due_date < today;
}
