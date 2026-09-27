"use client";

import { useState } from "react";

import { Button, MaskedText, Quote, StatusDot } from "@/shared/ui";

import { ConfirmDelete } from "./ConfirmDelete";
import { useSourceUtterances } from "../hooks/useSourceUtterances";
import { COLUMNS, COLUMN_LABELS, isCandidate } from "../types";
import type { ActionItemRead, ActionStatus } from "../types";

/**
 * S18. Why this item exists, and the two things a person does about it.
 *
 * ADR 0006 makes the extraction a draft the user finishes, which changes what
 * this panel is for. It is not a record of an item — it is the evidence the user
 * needs to decide whether the item is real, and the quotation is the whole of
 * that. Without it they would have to replay the meeting, which is the cost the
 * whole decision is trying to avoid.
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
  const quotation = useSourceUtterances(item);

  return (
    <aside
      aria-label="액션 아이템 상세"
      className="flex w-full max-w-[420px] flex-col border-l border-[var(--color-hairline)]"
      style={{
        background: "var(--color-surface-panel)",
        // `h-full` matched the left column's height, which is the whole page
        // once the decisions list and every card are on it -- so the drawer
        // opened wherever the page happened to be tall, usually well above the
        // card that was clicked. Sticky keeps it in the viewport at whatever
        // scroll position the click happened at instead: it travels with the
        // page up to this offset, then holds. Raised by a user reviewing a
        // 25-item board -- opening a card near the bottom put the drawer a
        // full page-height away.
        position: "sticky",
        top: "var(--space-page)",
        maxHeight: "calc(100vh - 2 * var(--space-page))",
      }}
    >
      <header
        className="flex items-start gap-3 border-b border-[var(--color-hairline)]"
        style={{ padding: "var(--space-card)" }}
      >
        <div className="min-w-0 flex-1">
          <h2
            className="text-[var(--color-ink-strong)]"
            style={{ fontSize: "var(--text-title)", fontWeight: "var(--text-title-weight)" }}
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
              {isCandidate(item) ? "후보" : COLUMN_LABELS[item.status ?? "needs_confirmation"]}
            </span>
            <span
              className="text-[var(--color-ink-muted)]"
              style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-metaSmall)" }}
            >
              {item.confidence.toFixed(2)}
            </span>
          </div>
        </div>
        <Button tone="quiet" size="compact" onClick={onClose} aria-label="닫기">
          닫기
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto" style={{ padding: "var(--space-card)" }}>
        <Field label="담당">{item.assignee_name ?? item.assignee_label ?? "미지정"}</Field>
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
                setFailure("상태를 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.");
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
              {quotation.sources.map((source) => (
                <Quote key={source.id}>
                  <MaskedText>{source.text}</MaskedText>
                </Quote>
              ))}
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
                  <span className="text-[var(--color-ink-body)]">{ref.system}</span>
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
                    <span className="ml-auto text-[var(--color-ink-muted)]">동기화 확인 중</span>
                  )}
                </div>
              ))}
            </div>
          </section>
        ) : null}
      </div>

      {failure !== null && (
        <p
          role="alert"
          className="text-[var(--color-signal-critical)]"
          style={{
            paddingInline: "var(--space-card)",
            fontSize: "var(--text-rowBody)",
            lineHeight: "var(--text-rowBody-leading)",
          }}
        >
          {failure}
        </p>
      )}

      <footer
        className="flex justify-end border-t border-[var(--color-hairline)]"
        style={{ padding: "var(--space-card)" }}
      >
        {/* Destructive actions are red text, then a modal. Red never fills a
            button, and there is no undo afterwards — the row is gone. */}
        <Button tone="destructiveText" onClick={() => setConfirming(true)}>
          삭제
        </Button>
      </footer>

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
  if (!item.source_utterance_ids?.length) {
    return "회의에서 뽑은 항목이 아니라 직접 추가한 항목입니다.";
  }
  if (quotation.loading) return "근거 발화를 불러오는 중입니다.";
  if (quotation.error) return "근거 발화를 불러오지 못했습니다.";
  return "근거 발화가 삭제되어 더 이상 볼 수 없습니다.";
}

function SectionTitle({ children }: { children: string }) {
  return (
    <h3
      className="text-[var(--color-ink-strong)]"
      style={{ fontSize: "var(--text-status)", fontWeight: "var(--text-status-weight)" }}
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
