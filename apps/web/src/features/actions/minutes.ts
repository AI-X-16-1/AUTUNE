import { COLUMN_LABELS, UNCONFIRMED_DECISION, isCandidate } from "./types";
import type { ActionItemRead, MeetingSummary } from "./types";

/**
 * One meeting as a page of minutes, in plain text, for somebody to paste where
 * their team keeps such things (the user, 2026-10-02).
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
 * Built in the browser from what the summary tab already holds. Nothing is
 * sent anywhere: copying is the reader's act, and so is where it is pasted.
 */

export function minutesText(summary: MeetingSummary, title?: string | null): string {
  const lines: string[] = [title ? `회의록 — ${title}` : "회의록", ""];

  lines.push("결정");
  if (summary.decisions.length === 0) {
    lines.push("- 없음");
  } else {
    for (const decision of summary.decisions) {
      lines.push(
        `- ${decision.statement}${decision.status === "pending" ? ` (${UNCONFIRMED_DECISION})` : ""}`,
      );
    }
  }

  const items = summary.action_items.filter((item) => !isCandidate(item));
  lines.push("", "액션");
  if (items.length === 0) {
    lines.push("- 없음");
  } else {
    for (const item of items) lines.push(actionLine(item));
  }

  const note = summary.note?.trim();
  if (note) lines.push("", "팀 메모", note);

  return lines.join("\n");
}

function actionLine(item: ActionItemRead): string {
  const who = item.assignee_name ?? item.assignee_label ?? "담당 미지정";
  const due = item.due_date ?? "기한 없음";
  // The generated contract type leaves `status` optional; the server always sends one.
  const status = COLUMN_LABELS[item.status ?? "needs_confirmation"];
  return `- [${status}] ${item.description} — ${who} · ${due}`;
}

/** The title of the meeting, where the summary's own rows carry one. */
export function titleOf(summary: MeetingSummary): string | null {
  return summary.action_items.find((item) => item.meeting_title)?.meeting_title ?? null;
}
