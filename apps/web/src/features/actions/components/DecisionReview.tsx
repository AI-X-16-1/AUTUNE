"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button, StatusDot, type StatusVariant } from "@/shared/ui";

import { ConfirmDelete } from "./ConfirmDelete";
import { useDecisionReview } from "../hooks/useDecisionReview";
import type { DecisionStatus, ReviewAmbiguous, ReviewDecision } from "../types";

/**
 * S15's decisions: what the meeting settled, as the model proposed it, for a
 * person to confirm before anything leaves Autune (#246, #247).
 *
 * The model proposes about twice as many decisions as a team meeting holds and a
 * third of them are right, so the list is a question, not a record. Pending ones
 * come first; confirmed ones are what confirm-and-send will send; rejected ones
 * stay visible, muted, so a mis-click can be taken back.
 *
 * "삭제" appears only on a decision a person added. A model's decision is
 * rejected rather than deleted — a deleted one would be proposed again by the
 * next run — and "거부" already says that.
 */
export function DecisionReview({ meetingId }: { meetingId: string }) {
  const { review, loading, error, setStatus, reword, add, remove } = useDecisionReview(meetingId);

  const decisions = review ? [...review.decisions].sort(byStatus) : [];

  return (
    <section aria-label="결정">
      <header className="mb-3 flex items-baseline gap-2 border-b border-[var(--color-hairline)] pb-2">
        <h2
          className="text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-status)", fontWeight: "var(--text-status-weight)" }}
        >
          결정
        </h2>
        {review !== null && (
          <span className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
            확인 대기 {review.pending_decisions}
          </span>
        )}
        {review !== null && <AddDecision onAdd={add} />}
      </header>

      {review === null ? (
        <Note>{error ? "이 회의의 결정을 불러오지 못했습니다." : loading ? "결정을 불러오는 중입니다." : ""}</Note>
      ) : (
        <>
          {error ? <Note>최신 결정을 불러오지 못해 이전 목록을 보여주고 있습니다.</Note> : null}
          {decisions.length === 0 ? (
            <Note>이 회의에서 제안된 결정이 없습니다. 놓친 결정은 직접 추가할 수 있습니다.</Note>
          ) : (
            <ul className="grid gap-2">
              {decisions.map((decision) => (
                <DecisionRow
                  key={decision.id}
                  decision={decision}
                  onStatus={(status) => setStatus(decision.id, status)}
                  onReword={(statement) => reword(decision.id, statement)}
                  onDelete={() => remove(decision.id)}
                />
              ))}
            </ul>
          )}
          <AmbiguousSummary items={review.ambiguous_agreements} />
        </>
      )}
    </section>
  );
}

const ORDER: Record<DecisionStatus, number> = { pending: 0, confirmed: 1, rejected: 2 };

function byStatus(a: ReviewDecision, b: ReviewDecision): number {
  return ORDER[a.status] - ORDER[b.status];
}

const STATUS: Record<DecisionStatus, { label: string; variant: StatusVariant }> = {
  pending: { label: "확인 대기", variant: "attention" },
  confirmed: { label: "확정", variant: "confirmed" },
  rejected: { label: "거부", variant: "idle" },
};

function DecisionRow({
  decision,
  onStatus,
  onReword,
  onDelete,
}: {
  decision: ReviewDecision;
  onStatus: (status: DecisionStatus) => Promise<unknown>;
  onReword: (statement: string) => Promise<unknown>;
  onDelete: () => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [pending, setPending] = useState(false);
  // Every change the row sends says so when it fails, the way the drawer does.
  const [failure, setFailure] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>, failed: string): Promise<boolean> {
    setPending(true);
    setFailure(null);
    try {
      await action();
      return true;
    } catch {
      setFailure(failed);
      return false;
    } finally {
      setPending(false);
    }
  }

  const status = STATUS[decision.status];
  const reworded = decision.statement !== decision.model_statement;

  return (
    <li
      className="border border-[var(--color-hairline)]"
      style={{ padding: "var(--space-card)", borderRadius: "var(--radius)" }}
    >
      <div className="flex items-start gap-2">
        <span className="mt-[7px]">
          <StatusDot variant={status.variant} hollow={decision.status === "pending"} />
        </span>
        <div className="min-w-0 flex-1">
          {editing ? (
            <RewordForm
              initial={decision.statement}
              pending={pending}
              onCancel={() => setEditing(false)}
              onSave={async (statement) => {
                if (await run(() => onReword(statement), "문장을 고치지 못했습니다. 잠시 후 다시 시도해 주세요.")) {
                  setEditing(false);
                }
              }}
            />
          ) : (
            <p
              className={
                decision.status === "rejected"
                  ? "text-[var(--color-ink-muted)] line-through"
                  : "text-[var(--color-ink-strong)]"
              }
              style={{ fontSize: "var(--text-body)", lineHeight: "var(--text-body-leading)" }}
            >
              {decision.statement}
            </p>
          )}
          {reworded && !editing ? (
            <p className="mt-1 text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
              모델 문장: {decision.model_statement}
            </p>
          ) : null}
          <p className="mt-1 text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
            {status.label} ·{" "}
            {decision.origin === "user"
              ? "직접 추가"
              : `근거 발화 ${decision.source_utterance_ids.length}건 · 신뢰도 ${Math.round(decision.confidence * 100)}%`}
          </p>
        </div>
      </div>

      {!editing ? (
        <div className="mt-2 flex flex-wrap justify-end gap-1">
          {decision.status === "pending" ? (
            <>
              <Button
                tone="text"
                size="compact"
                disabled={pending}
                onClick={() => run(() => onStatus("confirmed"), "확정하지 못했습니다. 잠시 후 다시 시도해 주세요.")}
              >
                확정
              </Button>
              <Button
                tone="quiet"
                size="compact"
                disabled={pending}
                onClick={() => run(() => onStatus("rejected"), "거부하지 못했습니다. 잠시 후 다시 시도해 주세요.")}
              >
                거부
              </Button>
            </>
          ) : (
            <Button
              tone="quiet"
              size="compact"
              disabled={pending}
              onClick={() => run(() => onStatus("pending"), "되돌리지 못했습니다. 잠시 후 다시 시도해 주세요.")}
            >
              확인 대기로 되돌리기
            </Button>
          )}
          {decision.status !== "rejected" ? (
            <Button tone="quiet" size="compact" disabled={pending} onClick={() => setEditing(true)}>
              문장 고치기
            </Button>
          ) : null}
          {decision.origin === "user" ? (
            <Button tone="destructiveText" size="compact" disabled={pending} onClick={() => setConfirmingDelete(true)}>
              삭제
            </Button>
          ) : null}
        </div>
      ) : null}

      {failure !== null && (
        <p
          role="alert"
          className="mt-2 text-[var(--color-signal-critical)]"
          style={{ fontSize: "var(--text-rowBody)", lineHeight: "var(--text-rowBody-leading)" }}
        >
          {failure}
        </p>
      )}

      {confirmingDelete ? (
        <ConfirmDelete
          noun="결정"
          description={decision.statement}
          pending={pending}
          onCancel={() => setConfirmingDelete(false)}
          onConfirm={async () => {
            await run(onDelete, "삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.");
            setConfirmingDelete(false);
          }}
        />
      ) : null}
    </li>
  );
}

/** Mirrors `DecisionReviewUpdate.statement` and `DecisionCreate.statement`. */
const STATEMENT_MAX = 2000;

function RewordForm({
  initial,
  pending,
  onCancel,
  onSave,
}: {
  initial: string;
  pending: boolean;
  onCancel: () => void;
  onSave: (statement: string) => Promise<void>;
}) {
  const [statement, setStatement] = useState(initial);
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    field.current?.focus();
  }, []);
  const trimmed = statement.trim();

  return (
    <form
      className="grid gap-2"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        if (trimmed && !pending) void onSave(trimmed);
      }}
    >
      <textarea
        ref={field}
        aria-label="결정 문장"
        value={statement}
        maxLength={STATEMENT_MAX}
        rows={2}
        onChange={(event) => setStatement(event.target.value)}
        className={CONTROL}
        style={CONTROL_STYLE}
      />
      <div className="flex justify-end gap-2">
        <Button type="button" tone="quiet" size="compact" onClick={onCancel} disabled={pending}>
          취소
        </Button>
        <Button type="submit" tone="text" size="compact" loading={pending} disabled={!trimmed}>
          저장
        </Button>
      </div>
    </form>
  );
}

function AddDecision({ onAdd }: { onAdd: (statement: string) => Promise<unknown> }) {
  const [open, setOpen] = useState(false);
  const [statement, setStatement] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const trimmed = statement.trim();

  if (!open) {
    return (
      <span className="ml-auto">
        <Button tone="text" size="compact" onClick={() => setOpen(true)}>
          + 결정 추가
        </Button>
      </span>
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!trimmed || pending) return;
    setPending(true);
    setError(null);
    try {
      await onAdd(trimmed);
      setStatement("");
      setOpen(false);
    } catch (cause) {
      // The typed text stays, as in the action-item form.
      setError(cause instanceof Error ? cause.message : "추가하지 못했습니다.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="ml-auto grid w-full max-w-[520px] gap-2" onSubmit={submit}>
      <textarea
        aria-label="추가할 결정"
        placeholder="회의에서 정한 것을 한 문장으로"
        value={statement}
        maxLength={STATEMENT_MAX}
        rows={2}
        onChange={(event) => setStatement(event.target.value)}
        className={CONTROL}
        style={CONTROL_STYLE}
        autoFocus
      />
      {error !== null && (
        <p role="alert" className="text-[var(--color-signal-critical)]" style={{ fontSize: "var(--text-rowBody)" }}>
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" tone="quiet" size="compact" onClick={() => setOpen(false)} disabled={pending}>
          취소
        </Button>
        <Button type="submit" tone="text" size="compact" loading={pending} disabled={!trimmed}>
          추가
        </Button>
      </div>
    </form>
  );
}

const OUTCOME: Record<ReviewAmbiguous["outcome"], string> = {
  not_asked: "아직 묻지 않음",
  pending: "답변 대기",
  undecided: "답이 없음",
  resolved: "답변 완료",
};

/**
 * The weak assents, as counts. Read-only: the speaker answers by DM, and who else
 * may answer for them is #246 point 1. No utterance text — the review carries
 * ids only, and quoting someone's hesitation on a shared screen is what the DM
 * to the speaker exists to avoid.
 */
function AmbiguousSummary({ items }: { items: ReviewAmbiguous[] }) {
  if (items.length === 0) return null;
  const counts = new Map<ReviewAmbiguous["outcome"], number>();
  for (const item of items) counts.set(item.outcome, (counts.get(item.outcome) ?? 0) + 1);

  return (
    <p className="mt-3 text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
      애매한 동의 {items.length}건 — 발화자 확인:{" "}
      {[...counts].map(([outcome, count]) => `${OUTCOME[outcome]} ${count}`).join(" · ")}
    </p>
  );
}

function Note({ children }: { children: string }) {
  if (!children) return null;
  return (
    <p className="mb-2 text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
      {children}
    </p>
  );
}

const CONTROL =
  "w-full rounded-[var(--radius)] bg-[var(--color-surface-paper)] text-[var(--color-ink-strong)] placeholder:text-[var(--color-ink-muted)] focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)]";

const CONTROL_STYLE = {
  border: "1px solid var(--color-hairline)",
  paddingInline: "var(--control-px-text)",
  paddingBlock: "var(--space-8)",
  fontSize: "var(--text-body)",
  lineHeight: "var(--text-body-leading)",
  fontFamily: "var(--font-sans)",
};
