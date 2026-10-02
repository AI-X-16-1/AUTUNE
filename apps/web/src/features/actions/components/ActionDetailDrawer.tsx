"use client";

import { useEffect, useState } from "react";

import { Button, MaskedText, Quote, StatusDot } from "@/shared/ui";

import { ConfirmDelete } from "./ConfirmDelete";
import { ContextLines } from "./ContextLines";
import { useSourceUtterances } from "../hooks/useSourceUtterances";
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
 */
export function ActionDetailDrawer({
  item,
  onClose,
  onStatusChange,
  onDelete,
}: {
  item: ActionItemRead;
  onClose: () => void;
  onStatusChange?: (status: ActionStatus) => void | Promise<void>;
  onDelete?: () => void | Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // What the last change the drawer sent failed with. Both changes say so here,
  // the way AddActionItem does for an add: the status select is controlled by
  // `item.status`, so a failed change otherwise just springs back with no
  // reason, and a failed delete otherwise closes the dialog and leaves the
  // drawer open saying nothing. Raised in review of #292.
  const [failure, setFailure] = useState<string | null>(null);
  // The select is controlled by `item.status`, so while a PATCH is in flight it
  // still shows the old value. Left enabled, a second pick sends a second PATCH
  // and the board ends on whichever response lands last. Raised in review of #292.
  const [changing, setChanging] = useState(false);
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
      aria-label="액션 아이템 상세"
      className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ background: "rgba(22,25,31,.35)", padding: "var(--space-page)" }}
      onClick={onClose}
    >
      <aside
        className="flex w-full max-w-[480px] flex-col overflow-y-auto"
        style={{
          background: "var(--color-surface-panel)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--shadow-overlay)",
          // Fixed to the viewport, so it opens where the person is looking
          // whatever the page's scroll -- the problem the sticky column once
          // solved for a card near the bottom of a 25-item board.
          maxHeight: "calc(100vh - 2 * var(--space-page))",
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
              {item.description}
            </h2>
            <div className="mt-1 flex items-center gap-2">
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
              <span
                className="text-[var(--color-ink-muted)]"
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--text-metaSmall)",
                }}
              >
                {item.confidence.toFixed(2)}
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
            <Button
              tone="destructiveText"
              size="compact"
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
            {item.needs_reassignment
              ? "재배정 필요 · 담당자가 이 팀에 없습니다"
              : (item.assignee_name ?? item.assignee_label ?? "미지정")}
          </Field>
          <Field label="기한" mono>
            {item.due_date ?? "없음"}
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
                setFailure(null);
                setChanging(true);
                try {
                  await onStatusChange?.(event.target.value as ActionStatus);
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
                  <Quote key={source.id}>
                    <MaskedText>{source.text}</MaskedText>
                  </Quote>
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

          {item.sync_refs?.length ? (
            <section className="mt-6">
              <SectionTitle>연동</SectionTitle>
              <div className="mt-2 grid gap-2">
                {item.sync_refs.map((ref) => (
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
};

function historyText(entry: EditHistoryEntry): string {
  if (entry.kind === "created") return "직접 추가함";
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
        fontSize: "var(--text-status)",
        fontWeight: "var(--text-status-weight)",
      }}
    >
      {children}
    </h3>
  );
}

function Field({
  label,
  mono = false,
  children,
}: {
  label: string;
  mono?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-4">
      <div
        className="text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        {label}
      </div>
      <div
        className="mt-1 text-[var(--color-ink-body)]"
        style={{
          fontSize: "var(--text-body)",
          fontFamily: mono ? "var(--font-mono)" : undefined,
        }}
      >
        {children}
      </div>
    </div>
  );
}
