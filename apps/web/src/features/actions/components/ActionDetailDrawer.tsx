"use client";

import { useState } from "react";

import { Button, MaskedText, Quote, StatusDot } from "@/shared/ui";

import { ConfirmDelete } from "./ConfirmDelete";
import { COLUMNS, COLUMN_LABELS, isCandidate } from "../types";
import type { ActionItem, ActionStatus } from "../types";

/**
 * One source utterance, resolved to text.
 *
 * Not part of `ActionItem`, which carries only `source_utterance_ids`. Nothing
 * on `/api/extraction` returns the text for them yet, so the drawer takes it as
 * a prop rather than fetching — see the note in the pull request. Reading
 * `utterances` directly is module A's, and this feature calls only its own API.
 */
export interface SourceUtterance {
  id: string;
  text: string;
}

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
  sources = [],
  onClose,
  onStatusChange,
  onDelete,
}: {
  item: ActionItem;
  sources?: SourceUtterance[];
  onClose: () => void;
  onStatusChange?: (status: ActionStatus) => void | Promise<void>;
  onDelete?: () => void | Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  return (
    <aside
      aria-label="액션 아이템 상세"
      className="flex h-full w-full max-w-[420px] flex-col border-l border-[var(--color-hairline)]"
      style={{ background: "var(--color-surface-panel)" }}
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
        <Field label="담당">{item.assignee_label ?? "미지정"}</Field>
        <Field label="기한" mono>
          {item.due_date ?? "없음"}
        </Field>

        <Field label="상태">
          <select
            value={item.status ?? "needs_confirmation"}
            onChange={(event) => void onStatusChange?.(event.target.value as ActionStatus)}
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
          {sources.length > 0 ? (
            <div className="mt-2 grid gap-2">
              {sources.map((source) => (
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
              {item.source_utterance_ids?.length
                ? "근거 발화를 불러오지 못했습니다."
                : "회의에서 뽑은 항목이 아니라 직접 추가한 항목입니다."}
            </p>
          )}
        </section>

        {item.external_refs?.length ? (
          <section className="mt-6">
            <SectionTitle>연동</SectionTitle>
            <div className="mt-2 grid gap-2">
              {item.external_refs.map((ref) => (
                <div
                  key={`${ref.system}-${ref.url}`}
                  className="flex items-center gap-2 border-b border-[var(--color-hairline)] pb-2"
                  style={{ fontSize: "var(--text-metaSmall)" }}
                >
                  <StatusDot variant={ref.url ? "confirmed" : "critical"} />
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
                    <span className="ml-auto text-[var(--color-signal-critical)]">연결 끊김</span>
                  )}
                </div>
              ))}
            </div>
          </section>
        ) : null}
      </div>

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
            try {
              await onDelete?.();
              onClose();
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
