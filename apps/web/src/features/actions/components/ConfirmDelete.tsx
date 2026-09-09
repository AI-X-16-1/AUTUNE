"use client";

import { Button } from "@/shared/ui";

/**
 * The modal a destructive action goes through (ui-spec section 0).
 *
 * Red text button → this → an accent-filled "삭제". Red never fills a button;
 * it belongs to elapsing time and failure.
 *
 * The copy says the deletion is permanent because it is. The row is removed on
 * the server — `privacy.md` allows no soft deletes and no tombstones holding
 * content — so there is no undo to offer afterwards, and telling the user
 * beforehand is the only place that fact fits.
 */
export function ConfirmDelete({
  description,
  onCancel,
  onConfirm,
  pending = false,
}: {
  description: string;
  onCancel: () => void;
  onConfirm: () => void;
  pending?: boolean;
}) {
  return (
    <div
      role="dialog"
      aria-modal
      aria-label="액션 아이템 삭제"
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(22,25,31,.35)" }}
      onClick={onCancel}
    >
      <div
        className="w-full max-w-[420px]"
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
          style={{ fontSize: "var(--text-title)", fontWeight: "var(--text-title-weight)" }}
        >
          이 액션 아이템을 삭제할까요?
        </h2>

        <p
          className="mt-2 text-[var(--color-ink-body)]"
          style={{ fontSize: "var(--text-body)", lineHeight: "var(--text-body-leading)" }}
        >
          <span className="text-[var(--color-ink-strong)]">{description}</span>
          <br />
          지운 항목은 되돌릴 수 없습니다. 근거 발화는 회의 기록에 그대로 남습니다.
        </p>

        <div className="mt-4 flex justify-end gap-2">
          <Button tone="quiet" onClick={onCancel} disabled={pending}>
            취소
          </Button>
          <Button tone="primary" onClick={onConfirm} loading={pending}>
            삭제
          </Button>
        </div>
      </div>
    </div>
  );
}
