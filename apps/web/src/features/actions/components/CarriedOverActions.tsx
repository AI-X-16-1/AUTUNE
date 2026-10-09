"use client";

import { useEffect, useState } from "react";

import { onAgentActed } from "@/shared/lib/agentActed";
import { Button, StatusDot } from "@/shared/ui";

import { getCarriedOver } from "../api";
import { isOverdue, shownDue } from "../dates";
import type { CarriedOver, CarriedOverItem } from "../types";
import { staleLabel } from "../stale";

/**
 * What the team's earlier meetings left open (PRD 5.2, WBS 4.8): a popup the
 * first time a meeting's review opens, and a quiet row above the board after.
 *
 * The popup opens once per meeting per browser: a review is opened many times,
 * and a modal every time would be dismissed unread. Whether it was seen is a
 * per-viewer convenience, so it lives in `localStorage`, and a browser that
 * refuses storage just sees the popup again.
 *
 * The ochre dot is S08's mark for a carried-over item; an overdue date is red
 * text, never a fill — red belongs to elapsing time (ui-spec section 0). The
 * list is read-only: an item is changed on its own meeting's board, where its
 * evidence is.
 *
 * Read again when the assistant changed something (#1055): a due date moved on
 * a chat card is one of these rows, and the counts above them. Only the first
 * answer may open the popup -- a later one changes what is shown and never
 * puts the popup back in front of someone who closed it, which a browser that
 * refuses storage would otherwise see on every approval.
 */
export function CarriedOverActions({ meetingId }: { meetingId: string }) {
  const [result, setResult] = useState<CarriedOver | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    let first = true;
    // Of two reads in flight only the later one lands.
    let latest = 0;
    const read = () => {
      const ticket = latest + 1;
      latest = ticket;
      getCarriedOver(meetingId)
        .then((answer) => {
          if (!alive || ticket !== latest) return;
          setResult(answer);
          if (first && answer.open > 0 && !seen(meetingId)) setOpen(true);
          first = false;
        })
        // Nothing to say when the read fails: the board below is the real work.
        .catch(() => undefined);
    };
    read();
    const stop = onAgentActed(read);
    return () => {
      alive = false;
      stop();
    };
  }, [meetingId]);

  if (result === null || result.open === 0) return null;

  const close = () => {
    markSeen(meetingId);
    setOpen(false);
  };

  return (
    <>
      <div
        className="flex items-center gap-2 border-b border-[var(--color-hairline)] pb-2"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        <StatusDot variant="attention" />
        <span className="flex-1 text-[var(--color-ink-body)]">
          지난 회의에서 넘어온 미완료 액션 {result.open}건
          {result.overdue > 0 ? (
            <span className="text-[var(--color-signal-critical)]">
              {" "}
              · 기한 지남 {result.overdue}건
            </span>
          ) : null}
          {result.stale ? (
            <span className="text-[var(--color-signal-attention)]">
              {" "}
              · 계속 밀림 {result.stale}건
            </span>
          ) : null}
        </span>
        <Button tone="text" size="compact" onClick={() => setOpen(true)}>
          보기
        </Button>
      </div>

      {open ? <Popup result={result} onClose={close} /> : null}
    </>
  );
}

function Popup({
  result,
  onClose,
}: {
  result: CarriedOver;
  onClose: () => void;
}) {
  const rest = result.open - result.items.length;
  return (
    <div
      role="dialog"
      aria-modal
      aria-label="지난 회의 미완료 액션"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(22,25,31,.35)" }}
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-[560px] flex-col"
        style={{
          background: "var(--color-surface-panel)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--shadow-overlay)",
          padding: "var(--space-card)",
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <h2
          className="text-[var(--color-ink-strong)]"
          style={{
            fontSize: "var(--text-title)",
            fontWeight: "var(--text-title-weight)",
          }}
        >
          지난 회의에서 넘어온 미완료 액션 {result.open}건
        </h2>
        <p
          className="mt-1 text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {result.overdue > 0
            ? `기한이 지난 ${result.overdue}건부터 보여드립니다.`
            : "기한이 가까운 순서로 보여드립니다."}
        </p>

        <ul className="mt-3 flex-1 overflow-y-auto">
          {result.items.map((item) => (
            <CarriedRow key={item.id} item={item} />
          ))}
        </ul>

        {rest > 0 ? (
          <p
            className="mt-2 text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-metaSmall)" }}
          >
            외 {rest}건은 각 회의의 액션 보드에서 확인할 수 있습니다.
          </p>
        ) : null}

        <div className="mt-4 flex justify-end">
          <Button tone="primary" onClick={onClose}>
            확인
          </Button>
        </div>
      </div>
    </div>
  );
}

function CarriedRow({ item }: { item: CarriedOverItem }) {
  const overdue = isOverdue(item);
  return (
    <li className="flex gap-2 border-b border-[var(--color-hairline)] py-2 last:border-b-0">
      <StatusDot variant="attention" className="mt-1.5" />
      <div className="min-w-0 flex-1">
        <div
          className="text-[var(--color-ink-strong)]"
          style={{
            fontSize: "var(--text-rowTitle)",
            fontWeight: "var(--text-rowTitle-weight)",
          }}
        >
          {item.description}
        </div>
        <div
          className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {staleLabel(item) ? (
            <span
              className="text-[var(--color-signal-attention)]"
              style={{ fontWeight: "var(--text-status-weight)" }}
            >
              {staleLabel(item)}
            </span>
          ) : null}
          <span className="text-[var(--color-ink-body)]">
            {item.needs_reassignment
              ? "재배정 필요"
              : (item.assignee_name ?? item.assignee_label ?? "담당 미지정")}
          </span>
          {item.due_date ? (
            <span
              style={{ color: overdue ? "var(--color-signal-critical)" : undefined }}
            >
              {shownDue(item.due_date)}
            </span>
          ) : null}
          <span>
            {item.status === "in_progress" ? "진행 중" : "진행 전"} ·{" "}
            {item.meeting_title}
            {item.meeting_started_at
              ? ` (${localDate(item.meeting_started_at)})`
              : ""}
          </span>
        </div>
      </div>
    </li>
  );
}

/**
 * The day a meeting was held, in the viewer's own time zone, as YYYY-MM-DD.
 * The server sends UTC; cutting its string would put a meeting held before
 * 09:00 in Seoul on the previous day. `sv-SE` is the locale whose date reads
 * that way.
 */
function localDate(iso: string): string {
  return new Date(iso).toLocaleDateString("sv-SE");
}

const SEEN_PREFIX = "autune.actions.carriedOverSeen.";

function seen(meetingId: string): boolean {
  try {
    return window.localStorage.getItem(SEEN_PREFIX + meetingId) !== null;
  } catch {
    return false;
  }
}

function markSeen(meetingId: string): void {
  try {
    window.localStorage.setItem(SEEN_PREFIX + meetingId, "1");
  } catch {
    // Storage refused: the popup opens again next time, which is harmless.
  }
}
