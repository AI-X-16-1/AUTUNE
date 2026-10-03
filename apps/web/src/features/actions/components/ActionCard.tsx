import { MaskedText, StatusDot } from "@/shared/ui";

import { isOverdue } from "../dates";
import { isCandidate } from "../types";
import type { ActionItemRead, SourceUtterance } from "../types";

/**
 * What a dragged card carries: the item's id under Autune's own type, and no
 * text. `text/plain` would let a card dropped on another application paste the
 * item's wording there -- meeting content leaving by a route nobody chose.
 */
const DRAG_TYPE = "application/x-autune-action-item";

/**
 * One card on the action board (S17).
 *
 * Reading order is fixed: title → reason → assignee and due date → issue key.
 * The reason sits second because ADR 0006 makes every item a draft the user
 * finishes, and the first thing they need is why the model thinks this is an
 * item at all.
 *
 * A broken integration link is red text, never a red fill — red belongs to
 * elapsing time and failure (ui-spec section 0).
 *
 * `showMeeting` puts the meeting's title above the title, for the board across
 * every meeting, where a card alone did not say which meeting it came from
 * (mentoring, 2026-10-01). One meeting's board leaves it off: every card there
 * would repeat the same title.
 *
 * `drag` makes the card something the board can move between columns. The
 * card is a `div` acting as a button rather than a `button` for that reason:
 * a drag that starts on a `button` does not begin in every browser. Enter and
 * Space open it as they did.
 */
export function ActionCard({
  item,
  selected = false,
  onSelect,
  showMeeting = false,
  drag,
  context,
}: {
  item: ActionItemRead;
  selected?: boolean;
  onSelect?: (id: string) => void;
  showMeeting?: boolean;
  /** Present on a board that moves cards; `moving` while its change is in flight. */
  drag?: { moving: boolean; onStart: () => void; onEnd: () => void };
  /**
   * The lines said just before this card's sentence, for a sentence that
   * says nothing by itself (`pointsAtNothing`). Shown under the title, muted,
   * and named as what they are so nobody reads a neighbouring line as the item.
   */
  context?: SourceUtterance[];
}) {
  const overdue = isOverdue(item);
  const draggable = drag !== undefined && !drag.moving;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect?.(item.id)}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onSelect?.(item.id);
      }}
      aria-current={selected}
      aria-busy={drag?.moving || undefined}
      draggable={draggable}
      onDragStart={
        draggable
          ? (event) => {
              event.dataTransfer.effectAllowed = "move";
              event.dataTransfer.setData(DRAG_TYPE, item.id);
              drag.onStart();
            }
          : undefined
      }
      onDragEnd={draggable ? drag.onEnd : undefined}
      className={`w-full border text-left ${draggable ? "cursor-grab" : "cursor-pointer"}`}
      style={{
        background: drag?.moving ? "var(--color-surface-sunken)" : "var(--color-surface-paper)",
        borderRadius: "var(--radius)",
        padding: "var(--space-card)",
        borderWidth: selected ? 1.5 : 1,
        borderColor: selected ? "var(--color-accent-default)" : "var(--color-hairline)",
      }}
    >
      {showMeeting && item.meeting_title ? (
        <div
          className="mb-1 truncate text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {item.meeting_title}
        </div>
      ) : null}
      <div
        className="text-[var(--color-ink-strong)]"
        style={{ fontSize: "var(--text-rowTitle)", fontWeight: "var(--text-rowTitle-weight)" }}
      >
        {item.description}
        {item.description_resolved ? (
          <span
            className="ml-1.5 text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-metaSmall)", fontWeight: 400 }}
            title="AI가 발화 속 지시어(그거, 저희 팀 등)를 풀어 다시 쓴 설명입니다. 원문과 다를 수 있어 확인이 필요합니다."
          >
            · AI 재구성
          </span>
        ) : null}
      </div>

      {context !== undefined && context.length > 0 ? (
        <div
          role="group"
          aria-label="앞선 발화"
          className="mt-1 border-l border-[var(--color-hairline)] pl-2 text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          <span>앞선 발화</span>
          {context.map((line) => (
            <p key={line.id}>
              <MaskedText>{line.text}</MaskedText>
            </p>
          ))}
        </div>
      ) : null}

      <div
        className="mt-1 text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        {reasonFor(item)}
      </div>

      <div className="mt-2 flex items-center gap-2" style={{ fontSize: "var(--text-metaSmall)" }}>
        {item.needs_reassignment ? (
          // Text, not a fill: red belongs to elapsing time and failure
          // (ui-spec section 0), and this is neither -- it is work nobody holds.
          <span
            className="text-[var(--color-ink-strong)]"
            style={{ fontWeight: "var(--text-status-weight)" }}
          >
            재배정 필요
          </span>
        ) : (
          <span className="text-[var(--color-ink-body)]">
            {item.assignee_name ?? item.assignee_label ?? "담당 미지정"}
          </span>
        )}
        {item.due_date ? (
          <span
            style={{
              fontFamily: "var(--font-mono)",
              color: overdue ? "var(--color-signal-critical)" : "var(--color-ink-muted)",
            }}
          >
            {item.due_date}
          </span>
        ) : null}
      </div>

      {item.sync_refs?.length ? (
        <div
          className="mt-2 flex items-center gap-2 text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {item.sync_refs.map((ref) => (
            <span
              key={ref.system}
              className="flex items-center gap-1"
              title={ref.url ? undefined : "동기화 확인 중"}
            >
              <StatusDot variant={ref.url ? "confirmed" : "progress"} />
              <span>{ref.system}</span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Why this card exists, in the user's terms.
 *
 * A candidate says so first. The point of showing a low-confidence item at all
 * is that the user can judge it, and a card that looks identical to a confident
 * one asks them to trust something the model did not.
 *
 * "직접 추가" is read from `origin`, not inferred from an empty source list. A
 * model item whose utterances were deleted with the transcript also has none,
 * and calling it hand-added would print the distinction edit cost is measured
 * on the wrong way round.
 */
/**
 * The line under the title: what to read to decide if the item is real.
 *
 * `summary` -- a preview of the sources beyond the title itself -- stands in
 * for the count when there is one; the full quotations stay drawer-only
 * either way, so this is never more than the one line was.
 */
function reasonFor(item: ActionItemRead): string {
  // Ahead of everything else: the text above may still carry what a PII
  // report corrected (#586), and only a person can say.
  if (item.needs_recheck) return "출처 발화가 정정됨 · 확인 필요";
  if (item.origin === "user") return "직접 추가";
  if (item.origin === "followup") return "후속 회의 제안";
  // Confirmed, a chat draft keeps only its summary: the server stops listing
  // its sources, so "근거 발화 0건" would misstate why.
  if (item.origin === "chat" && item.status !== "needs_confirmation") return "채팅으로 추가";
  const sources = item.source_utterance_ids?.length ?? 0;
  const deleted = item.deleted_source_count ?? 0;
  // ADR 0007: a model item whose evidence was deleted says so, rather than
  // printing "근거 발화 0건" as if the model had made it up.
  const base =
    sources === 0 && deleted > 0
      ? "근거 발화 삭제됨"
      : (item.summary ?? `근거 발화 ${sources}건`) + (deleted > 0 ? ` · ${deleted}건 삭제됨` : "");
  return isCandidate(item) ? `후보 · ${base}` : base;
}
