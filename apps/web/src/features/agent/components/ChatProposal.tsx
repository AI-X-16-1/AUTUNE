"use client";

import { useState } from "react";

import { ApiError } from "@/shared/api/client";
import { Button, MaskedText, StatusDot } from "@/shared/ui";

import { approvePending, rejectPending } from "../api";
import type { PendingAction, RejectReason } from "../types";

const REASONS: { value: RejectReason; label: string }[] = [
  { value: "wrong_evidence", label: "근거가 틀림" },
  { value: "not_now", label: "지금은 아님" },
  { value: "handled_elsewhere", label: "다른 곳에서 처리함" },
  { value: "other", label: "기타" },
];

const GONE = "더 이상 없는 제안입니다";
const UNKNOWN = "결과를 확인하지 못했습니다 — 승인 대기에서 확인해 주세요";
const TRY_LATER = "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";

function settled(item: PendingAction): { text: string; ok: boolean } {
  if (item.needs_check) return { text: UNKNOWN, ok: false };
  if (item.status === "approved") return { text: "승인했습니다", ok: true };
  if (item.status === "rejected") return { text: "거절했습니다", ok: true };
  if (item.status === "failed")
    return { text: "실행하지 못했습니다", ok: false };
  return { text: GONE, ok: false };
}

/**
 * One L2 proposal on an S34 reply, for a person who may decide it
 * (agent/docs/specs/2026-10-02-assistant-questions-design.md section 6). The
 * same endpoints and wording as 승인 대기; a 409, or a lost response on 승인
 * (the claim commits before the action runs), is reported, never retried.
 */
export function ChatProposal({ item }: { item: PendingAction }) {
  const [busy, setBusy] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [result, setResult] = useState<{ text: string; ok: boolean } | null>(
    null,
  );

  const decide = async (
    run: () => Promise<PendingAction>,
    approving: boolean,
  ) => {
    setBusy(true);
    try {
      setResult(settled(await run()));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409)
        setResult({ text: GONE, ok: false });
      else if (e instanceof ApiError && e.status === 404)
        setResult({ text: GONE, ok: false });
      else if (approving) setResult({ text: UNKNOWN, ok: false });
      else setResult({ text: TRY_LATER, ok: false });
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
        {item.title}
      </p>
      <p
        className="mt-1 whitespace-pre-wrap"
        style={{ fontSize: 12, lineHeight: 1.6 }}
      >
        <MaskedText>{item.body}</MaskedText>
      </p>
      {result ? (
        <p
          role="status"
          className="mt-2 flex items-center gap-2"
          style={{ fontSize: 12.5 }}
        >
          <StatusDot variant={result.ok ? "confirmed" : "critical"} />
          {result.text}
        </p>
      ) : choosing ? (
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
    </div>
  );
}
