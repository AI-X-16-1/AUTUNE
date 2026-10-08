import { isOverdue } from "./dates";
import { COLUMN_LABELS, UNCONFIRMED_DECISION, isCandidate } from "./types";
import type { ActionItemRead, MeetingSummary } from "./types";

/**
 * One meeting as a page of minutes (the user, 2026-10-02; as a document,
 * 2026-10-09): what the 요약 tab shows and what "회의록 복사" puts on the
 * clipboard, for somebody to paste where their team keeps such things.
 *
 * **One page, two renderings.** `minutesOf` decides what is on the page;
 * the tab draws it and `minutesText` writes it as plain text. Before, the tab
 * and the copy were built separately and listed different things -- the tab
 * had candidates and a line of what was said under each row, the copy had
 * neither -- so nobody could tell from the screen what they were about to
 * paste.
 *
 * **What the meeting settled and left to do -- not what was said.** Decisions
 * and action items as the review already lists them, and the team's memo. No
 * quotation: the lines an item was drawn from leave the server only for one
 * item at a time, in the detail window, and a page of minutes pasted into a
 * chat or a wiki is exactly the kind of place a transcript should not follow
 * its summary to. An item's own sentence is on the page as it is on the board
 * -- and where no model rewrote it, that sentence is what somebody said. It
 * is the same text that already goes to the team's Notion and Jira.
 *
 * **Candidates are left out.** A candidate is something the model was not sure
 * was an item at all; minutes that listed it would state a guess as an
 * outcome. An item still waiting for confirmation is listed, and says so; a
 * decision nobody confirmed is listed as one a model extracted.
 *
 * **A model's summary of the meeting heads the page only where the deployment
 * wrote one (#392), and says a model wrote it** -- on the screen and in the
 * copy alike, so the label goes wherever the paragraph is pasted.
 *
 * Built in the browser from what the summary tab already holds. Nothing is
 * sent anywhere: copying is the reader's act, and so is where it is pasted.
 */

/** The heading over a model's summary, wherever the page is read. */
export const WRITTEN_BY_MODEL = "AI 작성";

export interface Minutes {
  title: string;
  /** The day the meeting began, as the reader's own calendar has it. */
  day: string | null;
  overview: { text: string; points: string[] } | null;
  decisions: { id: string; statement: string; unconfirmed: boolean }[];
  actions: MinutesAction[];
  /** Items the page leaves out because the model was not sure of them. */
  candidates: number;
  note: string | null;
}

export interface MinutesAction {
  id: string;
  description: string;
  who: string;
  /** The day it is due, written as the page's date line writes a day. */
  due: string | null;
  overdue: boolean;
  /** Said after the line; nothing for an item simply not begun. */
  state: string | null;
}

export function minutesOf(summary: MeetingSummary, title?: string | null): Minutes {
  const items = summary.action_items.filter((item) => !isCandidate(item));
  const began = startOf(summary.meeting_started_at);
  return {
    title: title ? `회의록 — ${title}` : "회의록",
    day: began ? dayOf(began) : null,
    overview: summary.generated
      ? { text: summary.generated.overview, points: summary.generated.points }
      : null,
    decisions: summary.decisions.map((decision) => ({
      id: decision.id,
      statement: decision.statement,
      unconfirmed: decision.status === "pending",
    })),
    actions: items.map((item) => action(item, began)),
    candidates: summary.action_items.length - items.length,
    note: summary.note?.trim() || null,
  };
}

export function minutesText(summary: MeetingSummary, title?: string | null): string {
  const page = minutesOf(summary, title);
  const lines: string[] = [page.title];
  if (page.day) lines.push(page.day);

  if (page.overview) {
    lines.push("", `요약 (${WRITTEN_BY_MODEL})`, page.overview.text);
    for (const point of page.overview.points) lines.push(`- ${point}`);
  }

  lines.push("", "결정 사항");
  if (page.decisions.length === 0) lines.push("없음");
  page.decisions.forEach((decision, n) => {
    lines.push(
      `${n + 1}. ${decision.statement}${decision.unconfirmed ? ` (${UNCONFIRMED_DECISION})` : ""}`,
    );
  });

  lines.push("", "액션");
  if (page.actions.length === 0) lines.push("없음");
  page.actions.forEach((item, n) => {
    lines.push(`${n + 1}. ${item.description} — ${actionMeta(item)}`);
  });

  if (page.note) lines.push("", "메모", page.note);

  return lines.join("\n");
}

/**
 * Who, by when, and where it stands -- the same words on the tab and in the
 * copy, each set off by a dot: "김민경 · 10월 13일 (화) · 진행 중".
 *
 * The state is not in brackets (the user, 2026-10-09): a due date ends with
 * its weekday in them, and "10월 13일 (화) (진행 중)" read as two asides in a
 * row. One mark for every part, so the line has the same shape with or
 * without a date.
 */
export function actionMeta(item: MinutesAction): string {
  return [item.who, item.due ?? "기한 없음", ...(item.state ? [item.state] : [])].join(" · ");
}

function action(item: ActionItemRead, began: Date | null): MinutesAction {
  // The generated contract type leaves `status` optional; the server always sends one.
  const status = item.status ?? "needs_confirmation";
  return {
    id: item.id,
    description: item.description,
    who: item.needs_reassignment
      ? "재배정 필요"
      : (item.assignee_name ?? item.assignee_label ?? "담당 미지정"),
    due: dueOf(item.due_date, began),
    overdue: isOverdue(item),
    // "진행 전" is every item a meeting has just made; minutes that said it on
    // each line would say nothing.
    state: status === "todo" ? null : COLUMN_LABELS[status],
  };
}

/** When the meeting began, in the reader's time zone: the server sends UTC. */
function startOf(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? null : at;
}

/** "2026년 10월 8일 (목)". */
function dayOf(at: Date): string {
  // Written out, not `toLocaleDateString`: browsers and Node disagree about
  // the brackets around the weekday, and the copy must read the same from both.
  return `${at.getFullYear()}년 ${at.getMonth() + 1}월 ${at.getDate()}일 (${WEEKDAYS[at.getDay()]})`;
}

/**
 * A due date as the date line writes a day -- "10월 13일 (화)" -- and not as it
 * is stored, "2026-10-13" (the user, 2026-10-09).
 *
 * The year is the date line's to say, so it is written here only where
 * reading it from there would be wrong: a due date in another year than the
 * meeting's, and a page with no date line. A due date is a day on the
 * calendar with no time in it, so no time zone moves it.
 */
function dueOf(due: string | null | undefined, began: Date | null): string | null {
  if (!due) return null;
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(due);
  // Not a day as the server writes one: shown as it came, not guessed at.
  if (!parts) return due;
  const [year, month, day] = [Number(parts[1]), Number(parts[2]), Number(parts[3])];
  const weekday = WEEKDAYS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()];
  const written = `${month}월 ${day}일 (${weekday})`;
  return began && began.getFullYear() === year ? written : `${year}년 ${written}`;
}

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

/** The title of the meeting: its own, or the one its rows carry. */
export function titleOf(summary: MeetingSummary): string | null {
  return (
    summary.meeting_title ??
    summary.action_items.find((item) => item.meeting_title)?.meeting_title ??
    null
  );
}
