"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";

import { Button } from "@/shared/ui";

import type { ActionItemDraft } from "../api";

/**
 * Manual add, S17 (#64).
 *
 * ADR 0006 ranks recall above precision — a wrong item costs a click, a missing
 * one costs re-reading the meeting. Recall alone does not close that gap: the
 * user still has to be able to enter what the model never proposed at all.
 * Without this control the decision is a preference rather than a behaviour.
 *
 * Inline rather than a modal. `ConfirmDelete` is a modal because deletion is
 * irreversible and has to interrupt; adding is neither, and a dialog would cover
 * the board the user is reading the gap from.
 *
 * **There is no source-utterance picker, and there must not be one.** A
 * hand-added item has no utterance behind it — that is what "the model missed
 * it" means — so `source_utterance_ids` stays empty. Letting the user attach one
 * would put a provenance on the item that no model produced, and S18 renders the
 * empty state already.
 *
 * Confidence is not sent either. The server stores 1.0 for a typed item
 * (`schemas.ActionItemCreate`, which also forbids extra fields), because a
 * person entering an item is the certainty. A client-supplied score would put a
 * model number on a human judgement and corrupt the metric that compares them.
 */
export function AddActionItem({
  meetingId,
  onAdd,
}: {
  meetingId: string;
  onAdd: (draft: ActionItemDraft) => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <Button tone="text" size="compact" onClick={() => setOpen(true)}>
        + 액션 아이템 추가
      </Button>
    );
  }

  // Remounted on every open so a cancelled draft does not come back. The text
  // the user abandoned is not worth keeping around, and restoring it would make
  // "취소" mean something different the second time.
  return <AddForm meetingId={meetingId} onAdd={onAdd} onClose={() => setOpen(false)} />;
}

/**
 * Mirrors `ActionItemCreate`. The server validates; these are here so the limit
 * is visible while typing instead of arriving as a 422 afterwards.
 */
const DESCRIPTION_MAX = 2000;
const ASSIGNEE_LABEL_MAX = 200;

function AddForm({
  meetingId,
  onAdd,
  onClose,
}: {
  meetingId: string;
  onAdd: (draft: ActionItemDraft) => Promise<unknown>;
  onClose: () => void;
}) {
  const [description, setDescription] = useState("");
  const [assigneeLabel, setAssigneeLabel] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ids = useId();
  const first = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    first.current?.focus();
  }, []);

  const trimmed = description.trim();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!trimmed || pending) return;

    setPending(true);
    setError(null);
    try {
      await onAdd({
        meeting_id: meetingId,
        description: trimmed,
        assignee_label: assigneeLabel.trim() || null,
        due_date: dueDate || null,
      });
      onClose();
    } catch (cause) {
      // The typed text stays. Clearing it on a failed request would send the
      // user back to the transcript to reconstruct what they had just written,
      // which is the cost this whole feature exists to remove.
      setError(cause instanceof Error ? cause.message : "추가하지 못했습니다.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !pending) onClose();
      }}
      aria-label="액션 아이템 추가"
      className="grid gap-3 border border-[var(--color-hairline)]"
      style={{
        background: "var(--color-surface-panel)",
        borderRadius: "var(--radius)",
        padding: "var(--space-card)",
      }}
    >
      <FormField id={`${ids}-description`} label="할 일">
        <textarea
          ref={first}
          id={`${ids}-description`}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          maxLength={DESCRIPTION_MAX}
          rows={2}
          required
          placeholder="회의에서 정해졌지만 목록에 없는 일"
          className={CONTROL}
          style={CONTROL_STYLE}
        />
      </FormField>

      <div className="grid gap-3 sm:grid-cols-2">
        <FormField id={`${ids}-assignee`} label="담당자">
          <input
            id={`${ids}-assignee`}
            value={assigneeLabel}
            onChange={(event) => setAssigneeLabel(event.target.value)}
            maxLength={ASSIGNEE_LABEL_MAX}
            placeholder="이름"
            className={CONTROL}
            style={CONTROL_STYLE}
          />
        </FormField>

        <FormField id={`${ids}-due`} label="마감일">
          <input
            id={`${ids}-due`}
            type="date"
            value={dueDate}
            onChange={(event) => setDueDate(event.target.value)}
            className={CONTROL}
            style={CONTROL_STYLE}
          />
        </FormField>
      </div>

      {error !== null && (
        <p
          role="alert"
          className="text-[var(--color-signal-critical)]"
          style={{ fontSize: "var(--text-rowBody)", lineHeight: "var(--text-rowBody-leading)" }}
        >
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <Button type="button" tone="quiet" size="compact" onClick={onClose} disabled={pending}>
          취소
        </Button>
        {/* The one primary on this screen: the collapsed trigger is a text
            button, so a primary is only ever visible while this form is open. */}
        <Button type="submit" tone="primary" size="compact" loading={pending} disabled={!trimmed}>
          추가
        </Button>
      </div>
    </form>
  );
}

const CONTROL =
  "w-full rounded-[var(--radius)] bg-[var(--color-surface-paper)] text-[var(--color-ink-strong)] placeholder:text-[var(--color-ink-muted)] focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)]";

const CONTROL_STYLE = {
  // `--border-input` is a whole `border` shorthand, so it goes on `border`.
  // Assigning it to `border-color` is invalid CSS and the browser drops the
  // declaration silently, leaving the control with whatever colour it inherited.
  border: "var(--border-input)",
  paddingInline: "var(--control-px-text)",
  paddingBlock: "6px",
  fontSize: "var(--text-body)",
  lineHeight: "var(--text-body-leading)",
  fontFamily: "var(--font-sans)",
};

function FormField({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="grid gap-1">
      <label
        htmlFor={id}
        className="text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-rowLabel)", fontWeight: "var(--text-rowLabel-weight)" }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}
