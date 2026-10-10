"use client";

import { useEffect, useState } from "react";

import { Button, MaskedText, StatusDot } from "@/shared/ui";

import { AssigneeInput, assigneeFields, type AssigneeValue } from "./AssigneeInput";
import { ConfirmDelete } from "./ConfirmDelete";
import { ContextLines } from "./ContextLines";
import { SourceQuote } from "./SourceQuote";
import { SyncStatus } from "./SyncStatus";
import { useAssignable } from "../hooks/useAssignable";
import { useSourceUtterances } from "../hooks/useSourceUtterances";
import { CLOSED_NOTICE, CONFIRMED_NOTICE, confirms } from "../board";
import { shownDue } from "../dates";
import { shownLabel } from "../speaker";
import { typedTextRefusal } from "../refusal";
import { rowTitle } from "../title";
import { COLUMNS, COLUMN_LABELS, isCandidate } from "../types";
import type { ActionItemRead, ActionStatus, EditHistoryEntry } from "../types";

/**
 * S18. Why this item exists, and the two things a person does about it.
 *
 * ADR 0006 makes the extraction a draft the user finishes, which changes what
 * this panel is for. It is not a record of an item — it is the evidence the user
 * needs to decide whether the item is real, and the quotation is the whole of
 * that. Without it they would have to replay the meeting, which is the cost the
 * whole decision is trying to avoid.
 *
 * A small window over the board, not a column beside it (decided with the
 * user, 2026-10-01): opening it no longer reflows the board, and a click
 * outside it -- or Esc -- closes it and leaves the board exactly where it was.
 *
 * The two things a person does about the window itself sit together at the
 * top right, 삭제 then 닫기 (the user, 2026-10-02): they were at opposite ends,
 * the delete under everything a long item scrolls through. Side by side they
 * are told apart by colour -- red text for the one that destroys, as
 * everywhere -- and 삭제 still only opens the confirmation, so a slip costs a
 * second click, not the item.
 *
 * The heading is the line the card was opened by -- the item's short title, or
 * the sentence cut where it has none (`rowTitle`) -- and the whole sentence
 * stands under it (the user, 2026-10-09): the window used to open on the
 * sentence alone, under another heading than the card pressed. A sentence
 * short enough to be its own top line is shown once.
 */
export function ActionDetailDrawer({
  item,
  onClose,
  onStatusChange,
  onCloseUnfinished,
  onAssigneeChange,
  onDelete,
}: {
  item: ActionItemRead;
  onClose: () => void;
  onStatusChange?: (status: ActionStatus) => void | Promise<void>;
  /** Close the item without finishing it. The control is not shown without it. */
  onCloseUnfinished?: () => void | Promise<void>;
  /** Set the assignee: a member's account or a typed name, never both. */
  onAssigneeChange?: (change: {
    assignee_id: string | null;
    assignee_label: string | null;
  }) => void | Promise<void>;
  onDelete?: () => void | Promise<void>;
}) {
  const heading = rowTitle(item.title, item.description);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // What the last change the drawer sent failed with. Both changes say so here,
  // the way AddActionItem does for an add: the status select is controlled by
  // `item.status`, so a failed change otherwise just springs back with no
  // reason, and a failed delete otherwise closes the dialog and leaves the
  // drawer open saying nothing. Raised in review of #292.
  const [failure, setFailure] = useState<string | null>(null);
  // A status change out of "확인 필요" confirms the item, which copies it to
  // the tools the team connected. A drop on the board says so afterwards
  // (#712); the same change made here said nothing (review of #717).
  const [notice, setNotice] = useState<string | null>(null);
  // The select is controlled by `item.status`, so while a PATCH is in flight it
  // still shows the old value. Left enabled, a second pick sends a second PATCH
  // and the board ends on whichever response lands last. Raised in review of #292.
  const [changing, setChanging] = useState(false);
  // The assignee as it stands on the item, and what the person is typing
  // when they chose "직접 입력". A member or "미지정" is saved the moment it is
  // picked, like the status; a typed name is saved with its own button, since
  // saving on every keystroke would write half a name.
  const members = useAssignable(item.meeting_id);
  const stored: AssigneeValue = item.assignee_id
    ? { kind: "member", userId: item.assignee_id }
    : item.assignee_label
      ? { kind: "typed", label: item.assignee_label }
      : { kind: "none" };
  const [typed, setTyped] = useState<string | null>(null);
  const [assigning, setAssigning] = useState(false);

  const saveAssignee = async (value: AssigneeValue) => {
    setFailure(null);
    setAssigning(true);
    try {
      await onAssigneeChange?.(assigneeFields(value));
      setTyped(null);
    } catch (cause) {
      // A typed label is screened like any typed text (#1130); what was typed
      // stays in the field.
      setFailure(
        typedTextRefusal(cause) ?? "담당자를 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.",
      );
    } finally {
      setAssigning(false);
    }
  };
  // The quotation is fetched when the drawer opens (GET /action-items/{id});
  // the list the board holds carries utterance ids, never their words.
  // History follows an edit made here or on the board: the item's editable
  // fields are the revision the hook refetches on.
  // Esc closes the open layer: the delete dialog first, then this window.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (confirming) setConfirming(false);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirming, onClose]);
  const quotation = useSourceUtterances(
    item,
    [
      item.status,
      item.due_date,
      item.assignee_id,
      item.assignee_label,
      item.description,
    ].join("|"),
  );

  return (
    <div
      role="dialog"
      aria-modal
      aria-label="할 일 상세"
      // A panel at the right edge rather than a box in the middle: the board
      // the person opened it from stays in view to its left.
      className="fixed inset-0 z-40 flex items-stretch justify-end"
      style={{ background: "rgba(22,25,31,.35)", padding: "var(--space-12)" }}
      onClick={onClose}
    >
      <aside
        className="flex w-full max-w-[520px] flex-col overflow-y-auto"
        style={{
          background: "var(--color-surface-panel)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--shadow-overlay)",
          // Fixed to the viewport, so it opens where the person is looking
          // whatever the page's scroll -- the problem the sticky column once
          // solved for a card near the bottom of a 25-item board.
          maxHeight: "calc(100vh - 2 * var(--space-12))",
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <header
          className="flex items-start gap-3 border-b border-[var(--color-hairline)]"
          style={{ padding: "var(--space-card)" }}
        >
          <div className="min-w-0 flex-1">
            <h2
              className="text-[var(--color-ink-strong)]"
              style={{
                fontSize: "var(--text-title)",
                fontWeight: "var(--text-title-weight)",
              }}
            >
              {heading.shown}
            </h2>
            {heading.cut ? (
              <p
                className="mt-1 text-[var(--color-ink-body)]"
                style={{
                  fontSize: "var(--text-rowBody)",
                  lineHeight: "var(--text-rowBody-leading)",
                }}
              >
                {item.description}
              </p>
            ) : null}
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <StatusDot
                variant={isCandidate(item) ? "attention" : "progress"}
                hollow={item.status === "needs_confirmation"}
              />
              <span
                className="text-[var(--color-ink-muted)]"
                style={{ fontSize: "var(--text-metaSmall)" }}
              >
                {isCandidate(item)
                  ? "후보"
                  : COLUMN_LABELS[item.status ?? "needs_confirmation"]}
              </span>
              {/* A share a person can read; the raw score stays on hover. */}
              <span
                className="text-[var(--color-ink-muted)]"
                style={{ fontSize: "var(--text-metaSmall)" }}
                title={`AI 신뢰도 ${item.confidence.toFixed(2)}`}
              >
                · AI 신뢰도{" "}
                <span style={{ fontFamily: "var(--font-mono)" }}>
                  {Math.round(item.confidence * 100)}%
                </span>
              </span>
              {item.description_resolved ? (
                <span
                  className="text-[var(--color-ink-muted)]"
                  style={{ fontSize: "var(--text-metaSmall)" }}
                  title="AI가 발화 속 지시어(그거, 저희 팀 등)를 풀어 다시 쓴 설명입니다. 원문과 다를 수 있어 확인이 필요합니다."
                >
                  · AI 재구성
                </span>
              ) : null}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            {/* Destructive actions are red text, then a modal. Red never fills
                a button, and there is no undo afterwards — the row is gone. */}
            {/* Named for what it deletes: beside 닫기, a bare "삭제" read aloud
                does not say of what (review of #722). */}
            <Button
              tone="destructiveText"
              size="compact"
              aria-label="항목 삭제"
              onClick={() => setConfirming(true)}
            >
              삭제
            </Button>
            <Button tone="quiet" size="compact" onClick={onClose} aria-label="닫기">
              닫기
            </Button>
          </div>
        </header>

        <div
          className="flex-1 overflow-y-auto"
          style={{ padding: "var(--space-card)" }}
        >
          <Field label="담당">
            {onAssigneeChange === undefined ? (
              item.needs_reassignment ? (
                "재배정 필요 · 담당자가 이 팀에 없습니다"
              ) : (
                (item.assignee_name ?? shownLabel(item.assignee_label) ?? "미지정")
              )
            ) : (
              <div className="grid gap-2">
                {item.needs_reassignment ? (
                  <span>재배정 필요 · 담당자가 이 팀에 없습니다</span>
                ) : null}
                <AssigneeInput
                  id={`assignee-${item.id}`}
                  label="담당"
                  memberName={item.assignee_name}
                  members={members}
                  value={typed !== null ? { kind: "typed", label: typed } : stored}
                  disabled={assigning}
                  onChange={(value) => {
                    if (value.kind === "typed") setTyped(value.label);
                    else void saveAssignee(value);
                  }}
                  controlClassName="w-full border bg-transparent"
                  controlStyle={{
                    height: "var(--control-h-default)",
                    paddingInline: "var(--control-px-text)",
                    borderRadius: "var(--radius)",
                    border: "1px solid var(--color-hairline)",
                    fontSize: "var(--text-body)",
                  }}
                />
                {typed !== null && typed.trim() !== (item.assignee_label ?? "") ? (
                  <div>
                    <Button
                      tone="secondary"
                      size="compact"
                      loading={assigning}
                      onClick={() => void saveAssignee({ kind: "typed", label: typed })}
                    >
                      이름 저장
                    </Button>
                  </div>
                ) : null}
              </div>
            )}
          </Field>
          <Field label="기한">
            {item.due_date ? shownDue(item.due_date) : "없음"}
          </Field>
          {item.due_text ? (
            <Field label="기한 파싱 원문">
              <MaskedText>{item.due_text}</MaskedText>
            </Field>
          ) : null}

          <Field label="상태">
            <select
              value={item.status ?? "needs_confirmation"}
              disabled={changing}
              aria-busy={changing || undefined}
              onChange={async (event) => {
                const next = event.target.value as ActionStatus;
                const confirmed = confirms(item, next);
                setFailure(null);
                setNotice(null);
                setChanging(true);
                try {
                  await onStatusChange?.(next);
                  if (confirmed) setNotice(CONFIRMED_NOTICE);
                } catch {
                  setFailure(
                    "상태를 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.",
                  );
                } finally {
                  setChanging(false);
                }
              }}
              className="w-full border bg-transparent"
              style={{
                height: "var(--control-h-default)",
                paddingInline: "var(--control-px-text)",
                borderRadius: "var(--radius)",
                // See AddActionItem: `--border-input` has no dark value.
                border: "1px solid var(--color-hairline)",
                fontSize: "var(--text-body)",
              }}
            >
              {COLUMNS.map((status) => (
                <option key={status} value={status}>
                  {COLUMN_LABELS[status]}
                </option>
              ))}
            </select>
          </Field>
          {/* Under the status it changes, not at the top right: the button
              there says 닫기 and closes this window. Open items only -- one
              still waiting has nothing agreed to close, and a finished one is
              finished. No confirmation (the user, 2026-10-09): the status
              above re-opens it. */}
          {onCloseUnfinished !== undefined &&
          (item.status === "todo" || item.status === "in_progress") ? (
            <div className="mt-2">
              <Button
                tone="quiet"
                size="compact"
                disabled={changing}
                onClick={async () => {
                  setFailure(null);
                  setNotice(null);
                  setChanging(true);
                  try {
                    await onCloseUnfinished();
                    setNotice(CLOSED_NOTICE);
                  } catch {
                    setFailure("닫지 못했습니다. 잠시 후 다시 시도해 주세요.");
                  } finally {
                    setChanging(false);
                  }
                }}
              >
                끝내지 않고 닫기
              </Button>
            </div>
          ) : null}
          {notice !== null ? (
            <p
              role="status"
              className="mt-2 text-[var(--color-ink-muted)]"
              style={{ fontSize: "var(--text-metaSmall)" }}
            >
              {notice}
            </p>
          ) : null}

          <section className="mt-6">
            <SectionTitle>근거 발화</SectionTitle>
            {quotation.sources && quotation.sources.length > 0 ? (
              <div className="mt-2 grid gap-2">
                <ContextLines lines={quotation.related} label="요약에 쓴 발화" />
                <ContextLines
                  lines={quotation.context.filter(
                    (line) => !quotation.related.some((cited) => cited.id === line.id),
                  )}
                />
                {quotation.sources.map((source) => (
                  <SourceQuote key={source.id} source={source} />
                ))}
                {item.deleted_source_count > 0 ? (
                  <p
                    className="text-[var(--color-ink-muted)]"
                    style={{ fontSize: "var(--text-metaSmall)" }}
                  >
                    {`그 밖의 근거 발화 ${item.deleted_source_count}건은 삭제되었습니다.`}
                  </p>
                ) : null}
              </div>
            ) : (
              <p
                className="mt-2 text-[var(--color-ink-muted)]"
                style={{ fontSize: "var(--text-metaSmall)" }}
              >
                {quotationNote(item, quotation)}
              </p>
            )}
          </section>

          {item.sync_refs?.length || quotation.dmUrl ? (
            <section className="mt-6">
              <SectionTitle>연동</SectionTitle>
              <div className="mt-2 grid gap-2">
                {quotation.dmUrl ? (
                  // The reader's own confirmation DM: the server sends this to
                  // the person it went to and nobody else (#680).
                  <div
                    className="flex items-center gap-2 border-b border-[var(--color-hairline)] pb-2"
                    style={{ fontSize: "var(--text-metaSmall)" }}
                  >
                    <StatusDot variant="confirmed" />
                    <span className="text-[var(--color-ink-body)]">
                      Slack 확인 DM
                    </span>
                    <span className="text-[var(--color-ink-muted)]">
                      나에게 온 DM
                    </span>
                    <a
                      href={quotation.dmUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-auto text-[var(--color-accent-text)]"
                    >
                      열기
                    </a>
                  </div>
                ) : null}
                {(item.sync_refs ?? []).map((ref) => (
                  <div
                    key={ref.system}
                    className="flex items-center gap-2 border-b border-[var(--color-hairline)] pb-2"
                    style={{ fontSize: "var(--text-metaSmall)" }}
                  >
                    <StatusDot variant={ref.url ? "confirmed" : "progress"} />
                    <span className="text-[var(--color-ink-body)]">
                      {ref.system}
                    </span>
                    {ref.external_id ? (
                      <span
                        className="text-[var(--color-ink-muted)]"
                        style={{ fontFamily: "var(--font-mono)" }}
                      >
                        {ref.external_id}
                      </span>
                    ) : null}
                    {ref.url ? (
                      <a
                        href={ref.url}
                        target="_blank"
                        rel="noreferrer"
                        className="ml-auto text-[var(--color-accent-text)]"
                      >
                        열기
                      </a>
                    ) : (
                      <span className="ml-auto text-[var(--color-ink-muted)]">
                        동기화 확인 중
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {(item.sync_failures?.length ?? 0) > 0 || quotation.calendar ? (
            <section className="mt-6">
              <SectionTitle>연동 상태</SectionTitle>
              <SyncStatus item={item} calendar={quotation.calendar ?? null} />
            </section>
          ) : null}

          {quotation.history ? (
            <section className="mt-6">
              <SectionTitle>이력</SectionTitle>
              {quotation.history.length > 0 ? (
                <ol
                  className="mt-2 grid gap-1"
                  style={{ fontSize: "var(--text-metaSmall)" }}
                >
                  {quotation.history.map((entry, index) => (
                    <li key={`${entry.at}-${index}`} className="flex gap-2">
                      <span
                        className="text-[var(--color-ink-muted)]"
                        style={{ fontFamily: "var(--font-mono)" }}
                      >
                        {historyTime(entry.at)}
                      </span>
                      <span className="text-[var(--color-ink-body)]">
                        {historyText(entry)}
                      </span>
                    </li>
                  ))}
                </ol>
              ) : (
                <p
                  className="mt-2 text-[var(--color-ink-muted)]"
                  style={{ fontSize: "var(--text-metaSmall)" }}
                >
                  모델이 추출한 뒤로 고친 적이 없습니다.
                </p>
              )}
            </section>
          ) : null}
        </div>

        {failure !== null && (
          <p
            role="alert"
            className="text-[var(--color-signal-critical)]"
            style={{
              // The window ends here now that the footer is gone.
              padding: "0 var(--space-card) var(--space-card)",
              fontSize: "var(--text-rowBody)",
              lineHeight: "var(--text-rowBody-leading)",
            }}
          >
            {failure}
          </p>
        )}

        {confirming ? (
          <ConfirmDelete
            description={item.description}
            pending={deleting}
            onCancel={() => setConfirming(false)}
            onConfirm={async () => {
              setDeleting(true);
              setFailure(null);
              try {
                await onDelete?.();
                onClose();
              } catch {
                setFailure("삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.");
              } finally {
                setDeleting(false);
                setConfirming(false);
              }
            }}
          />
        ) : null}
      </aside>
    </div>
  );
}

/**
 * What the evidence section says when it has no quotation to show.
 *
 * Four different situations, and telling them apart is the point: "직접 추가"
 * is an answer, "불러오는 중" and "불러오지 못했습니다" are about the
 * connection, and a model item whose utterances are gone is about the meeting
 * — its transcript was deleted underneath the item.
 */
function quotationNote(
  item: ActionItemRead,
  quotation: { loading: boolean; error: Error | null },
): string {
  if (item.origin === "user") {
    return "회의에서 뽑은 항목이 아니라 직접 추가한 항목입니다.";
  }
  if (item.origin === "followup") {
    return "회의 뒤 후속 회의 에이전트가 제안한 항목이라 근거 발화가 없습니다.";
  }
  if (item.origin === "chat" && item.status !== "needs_confirmation") {
    return "채팅으로 만든 항목은 확정한 뒤에는 원본 발화를 보여주지 않습니다.";
  }
  // The server lists only utterances that still exist (ADR 0007), so an item
  // whose every source was deleted arrives with an empty list, not a list of
  // ids that fail to load.
  if (!item.source_utterance_ids?.length) {
    return "근거 발화가 삭제되어 더 이상 볼 수 없습니다.";
  }
  if (quotation.loading) return "근거 발화를 불러오는 중입니다.";
  if (quotation.error) return "근거 발화를 불러오지 못했습니다.";
  return "근거 발화가 삭제되어 더 이상 볼 수 없습니다.";
}

/** Field names as a person reads them. Only names are kept, never values (#109). */
const FIELD_LABELS: Record<string, string> = {
  description: "설명",
  assignee_id: "담당자",
  assignee_label: "담당자",
  due_date: "기한",
  status: "상태",
  project_id: "프로젝트",
};

function historyText(entry: EditHistoryEntry): string {
  if (entry.kind === "created") return "직접 추가함";
  if (entry.kind === "closed") return "끝내지 않고 닫힘";
  const labels = [
    ...new Set(entry.fields.map((field) => FIELD_LABELS[field] ?? field)),
  ];
  return labels.length > 0 ? `${labels.join("·")} 수정` : "수정함";
}

function historyTime(at: string): string {
  return new Date(at).toLocaleString("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function SectionTitle({ children }: { children: string }) {
  return (
    <h3
      className="text-[var(--color-ink-strong)]"
      style={{
        fontSize: "var(--text-heading)",
        fontWeight: "var(--text-heading-weight)",
      }}
    >
      {children}
    </h3>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      <div
        className="text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-label)", fontWeight: "var(--text-label-weight)" }}
      >
        {label}
      </div>
      <div
        className="mt-1.5 text-[var(--color-ink-body)]"
        style={{ fontSize: "var(--text-body)" }}
      >
        {children}
      </div>
    </div>
  );
}
