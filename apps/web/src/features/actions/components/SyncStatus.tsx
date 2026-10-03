"use client";

import { useState } from "react";

import { Button, StatusDot } from "@/shared/ui";

import { retrySync } from "../api";
import type { ActionItemRead, CalendarState, SyncFailure } from "../types";

/**
 * What the detail window says about an item's copies beyond the links it
 * already lists (#680): the ones that failed, and why there is no calendar
 * event when there is none.
 *
 * **A failure is red text** — ui-spec S17's "broken link in red text"; red
 * belongs to failure and elapsing time, never to a fill. What is known about it
 * is its kind, so that is all that is said: the outside service's own message
 * is not kept anywhere.
 *
 * **No event is usually not a failure.** An item with no date, or with a typed
 * name for an assignee, was never going to have one. The window names the
 * first thing missing, so somebody who added an item by hand is not left
 * asking why nothing appeared.
 *
 * **Past what the item lacks, the calendar is the assignee's own.** Whether an
 * event is there, that none is, a failed calendar copy -- each says whether
 * that person connected a calendar, so the server sends them to the assignee
 * and to nobody else, and this draws only what it was sent. Hence "내 캘린더"
 * in those lines: whoever reads them is the assignee.
 *
 * "다시 시도" queues the same sync an edit does. It answers before the sync
 * runs, so the window says it was sent again and does not claim it worked.
 */

export const SYSTEM_LABEL: Record<SyncFailure["system"], string> = {
  notion: "Notion",
  jira: "Jira",
  calendar: "캘린더",
};

const WHAT_FAILED: Record<Exclude<SyncFailure["kind"], "unreachable">, string> = {
  privacy:
    "개인정보로 보이는 값이 있어 보내지 않았습니다. 설명을 고친 뒤 다시 시도해 주세요.",
  reconnect: "연결이 끊어졌습니다. 다시 연결한 뒤 다시 시도해 주세요.",
  rejected: "요청이 거절되었습니다.",
};

/**
 * No answer is not "nothing arrived": a create that timed out on our side may
 * have reached the service, and sending again would make a second page, issue
 * or event (review of #754). So it asks the person to look there first.
 */
export function whatFailed(failure: SyncFailure): string {
  if (failure.kind === "unreachable") {
    const where = SYSTEM_LABEL[failure.system];
    return `응답이 없었습니다. 이미 만들어졌을 수 있으니, 다시 시도하기 전에 ${where}에서 먼저 확인해 주세요.`;
  }
  return WHAT_FAILED[failure.kind];
}

const NO_EVENT: Record<NonNullable<CalendarState["reason"]>, string> = {
  not_confirmed: "확정되면 담당자의 캘린더에 올라갑니다.",
  no_due_date: "기한이 없어 캘린더에 올리지 않았습니다.",
  no_account:
    "담당자가 이름으로만 적혀 있어 올릴 캘린더가 없습니다. 담당자를 팀 구성원으로 지정해 주세요.",
  not_on_team: "담당자가 이 팀의 구성원이 아니어서 캘린더에 올리지 않았습니다.",
  not_connected:
    "내 Google 캘린더가 연결되어 있지 않습니다. 설정 › 연동에서 연결하면 올라갑니다.",
};

export function calendarLine(calendar: CalendarState): string {
  if (calendar.state === "sent") return "내 캘린더에 올라가 있습니다.";
  return calendar.reason ? NO_EVENT[calendar.reason] : "내 캘린더에 아직 일정이 없습니다.";
}

const meta = { fontSize: "var(--text-metaSmall)" } as const;

export function SyncStatus({
  item,
  calendar,
}: {
  item: Pick<ActionItemRead, "id" | "sync_failures">;
  calendar: CalendarState | null;
}) {
  const failures = item.sync_failures ?? [];
  const [retry, setRetry] = useState<"idle" | "sending" | "sent" | "nothing" | "failed">(
    "idle",
  );

  if (failures.length === 0 && calendar === null) return null;

  const again = async () => {
    setRetry("sending");
    try {
      // `queued: false` is the server saying nothing went: the item has
      // nothing outside to follow it (review of #754).
      const { queued } = await retrySync(item.id);
      setRetry(queued ? "sent" : "nothing");
    } catch {
      setRetry("failed");
    }
  };

  return (
    <div className="mt-2 grid gap-2">
      {calendar !== null ? (
        <div
          className="flex items-center gap-2 border-b border-[var(--color-hairline)] pb-2"
          style={meta}
        >
          <StatusDot variant={calendar.state === "sent" ? "confirmed" : "progress"} />
          <span className="text-[var(--color-ink-body)]">캘린더</span>
          <span className="text-[var(--color-ink-muted)]">{calendarLine(calendar)}</span>
        </div>
      ) : null}

      {failures.map((failure) => (
        <p
          key={failure.system}
          className="text-[var(--color-signal-critical)]"
          style={meta}
        >
          {SYSTEM_LABEL[failure.system]} 연동 실패 · {whatFailed(failure)}
        </p>
      ))}

      {failures.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            tone="secondary"
            size="compact"
            onClick={() => void again()}
            loading={retry === "sending"}
            disabled={retry === "sending"}
          >
            다시 시도
          </Button>
          {retry === "sent" ? (
            <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>
              다시 보냈습니다. 잠시 뒤 목록을 새로 열면 결과가 보입니다.
            </span>
          ) : null}
          {retry === "nothing" ? (
            <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>
              보낼 것이 없습니다. 항목을 확정하면 다시 보냅니다.
            </span>
          ) : null}
          {retry === "failed" ? (
            <span role="alert" className="text-[var(--color-signal-critical)]" style={meta}>
              다시 보내지 못했습니다. 잠시 후 다시 시도해 주세요.
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
