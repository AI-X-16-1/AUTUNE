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

export const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

/**
 * A due date the server writes as `2026-10-13`, as these pages write one --
 * "10월 13일 화" (the user, 2026-10-09) -- or `null` when `iso` is not such a
 * day. The weekday has no bracket of its own: a decision's deadline sits inside
 * one already, "(담당 박지영, 기한 10월 13일 화)".
 *
 * The year is written only when it is not `year`, the one the page already
 * says or the reader takes for granted; `null` is a page that says none. A due
 * date is a day on the calendar with no time in it, so no time zone moves it.
 */
export function writtenDay(iso: string, year: number | null): string | null {
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!parts) return null;
  const [y, month, day] = [Number(parts[1]), Number(parts[2]), Number(parts[3])];
  const weekday = WEEKDAYS[new Date(Date.UTC(y, month - 1, day)).getUTCDay()];
  const written = `${month}월 ${day}일 ${weekday}`;
  return y === year ? written : `${y}년 ${written}`;
}

/**
 * An item's due date on a card, a row or the drawer, where nothing on the
 * screen says a year: this year is the one left out. What is not a day as the
 * server writes one is shown as it came. An input keeps the stored form.
 */
export function shownDue(iso: string): string {
  return writtenDay(iso, new Date().getFullYear()) ?? iso;
}
