"use client";

import Link from "next/link";
import { useState } from "react";

import { ApiError } from "@/shared/api/client";
import { Button, MaskedText, StatusDot } from "@/shared/ui";

import { approvePending, listPending, rejectPending } from "../api";
import { reportLink } from "../reportLink";
import type { PendingAction, RejectReason } from "../types";

const REASONS: { value: RejectReason; label: string }[] = [
  { value: "wrong_evidence", label: "근거가 틀림" },
  { value: "not_now", label: "지금은 아님" },
  { value: "handled_elsewhere", label: "다른 곳에서 처리함" },
  { value: "other", label: "기타" },
];

// Wording copied from ApprovalsScreen so both surfaces say the same thing.
const RESULT_LABEL: Record<PendingAction["status"], string> = {
  pending: "처리했습니다",
  approved: "승인했습니다",
  rejected: "거절했습니다",
  failed: "실행하지 못했습니다",
  superseded: "다른 제안으로 대체됨",
};

const NEEDS_CHECK = "결과를 확인하지 못했습니다 — 직접 확인해 주세요";
const GONE = "더 이상 없는 제안입니다";
const TRY_LATER = "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";

function settled(item: PendingAction): { text: string; ok: boolean } {
  if (item.needs_check) return { text: NEEDS_CHECK, ok: false };
  const ok = item.status === "approved" || item.status === "rejected";
  return { text: RESULT_LABEL[item.status] ?? "처리했습니다", ok };
}

/**
 * Whether the server may have acted although the request failed: a 5xx or a
 * lost response on 승인 (the claim commits before the action runs). Same rule
 * as ApprovalsScreen.
 */
function outcomeUnknown(e: unknown, approving: boolean): boolean {
  if (e instanceof ApiError) return approving && e.status >= 500;
  return approving;
}

/**
 * One L2 proposal on an S34 reply, for a person who may decide it
 * (agent/docs/specs/2026-10-02-assistant-questions-design.md section 6). The
 * same endpoints and wording as 승인 대기; a 409, or a lost response on 승인
 * (the claim commits before the action runs), re-reads the queue, never retries.
 */
export function ChatProposal({ item }: { item: PendingAction }) {
  const [busy, setBusy] = useState(false);
  const [choosing, setChoosing] = useState(false);
  // A failure the person can retry: shown beside the buttons, which stay.
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ text: string; ok: boolean } | null>(
    null,
  );

  /**
   * After a 409 or a lost 승인, ask the queue what happened rather than guess:
   * gone (someone settled it), needing a check, or still pending (buttons
   * again). If even the queue cannot be read, ask for a check and offer
   * nothing to press twice -- the same rule as 승인 대기.
   */
  const reread = async () => {
    try {
      const now = (await listPending()).find((p) => p.id === item.id);
      if (!now) setResult({ text: GONE, ok: false });
      else if (now.needs_check) setResult({ text: NEEDS_CHECK, ok: false });
      else setResult(null);
    } catch {
      setResult({ text: NEEDS_CHECK, ok: false });
    }
  };

  const decide = async (
    run: () => Promise<PendingAction>,
    approving: boolean,
  ) => {
    setBusy(true);
    setError(null);
    try {
      setResult(settled(await run()));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404)
        setResult({ text: GONE, ok: false });
      else if (
        (e instanceof ApiError && e.status === 409) ||
        outcomeUnknown(e, approving)
      )
        await reread();
      else setError(TRY_LATER);
    } finally {
      setBusy(false);
      setChoosing(false);
    }
  };

  return (
    <div
      className="mt-3 rounded-[var(--radius)]"
      style={{ padding: 12, background: "var(--color-surface-paper)" }}
    >
      <p
        className="text-[var(--color-ink-strong)]"
        style={{ fontSize: 12.5, fontWeight: 600 }}
      >
        <MaskedText>{item.title}</MaskedText>
      </p>
      <p
        className="mt-1 whitespace-pre-wrap"
        style={{ fontSize: 12, lineHeight: 1.6 }}
      >
        <MaskedText>{item.body}</MaskedText>
      </p>
      {reportLink(item) && (
        <Link
          href={reportLink(item)!}
          className="mt-1 inline-block text-[var(--color-accent-default)]"
          style={{ fontSize: 12, fontWeight: 600 }}
        >
          대시보드에서 리포트 보기
        </Link>
      )}
      {result ? (
        <p
          role="status"
          className="mt-2 flex items-center gap-2"
          style={{ fontSize: 12.5 }}
        >
          <StatusDot variant={result.ok ? "confirmed" : "critical"} />
          {result.text}
        </p>
      ) : (
        <>
          {error && (
            <p
              role="alert"
              className="mt-2 flex items-center gap-2"
              style={{ fontSize: 12.5 }}
            >
              <StatusDot variant="critical" />
              {error}
            </p>
          )}
          {choosing ? (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {REASONS.map((r) => (
                <Button
                  key={r.value}
                  size="compact"
                  tone="secondary"
                  disabled={busy}
                  onClick={() =>
                    void decide(() => rejectPending(item.id, r.value), false)
                  }
                >
                  {r.label}
                </Button>
              ))}
              <Button
                size="compact"
                tone="quiet"
                disabled={busy}
                onClick={() => setChoosing(false)}
              >
                취소
              </Button>
            </div>
          ) : (
            <div className="mt-2 flex gap-1.5">
              <Button
                size="compact"
                tone="primary"
                loading={busy}
                onClick={() => void decide(() => approvePending(item.id), true)}
              >
                승인
              </Button>
              <Button
                size="compact"
                tone="quiet"
                disabled={busy}
                onClick={() => setChoosing(true)}
              >
                거절
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
