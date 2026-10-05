"use client";

import { useState } from "react";

import { Button } from "@/shared/ui";

/**
 * The 확인 필요 column's bar for working through drafts several at a time
 * (the user, 2026-10-04): select all, confirm the selected, delete the
 * selected. Deleting asks once, in place, the way a single delete does.
 */
export function BulkTriage({
  total,
  picked,
  busy,
  onPickAll,
  onConfirm,
  onDelete,
}: {
  total: number;
  picked: number;
  busy: boolean;
  onPickAll: (all: boolean) => void;
  onConfirm: () => void;
  onDelete: () => void;
}) {
  const [asking, setAsking] = useState(false);
  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  if (total === 0) return null;
  const all = picked === total;

  return (
    <div
      className="mt-2 flex flex-wrap items-center gap-2"
      style={meta}
      aria-label="여러 개 한 번에"
    >
      <label className="flex items-center gap-1 text-[var(--color-ink-muted)]">
        <input
          type="checkbox"
          checked={all}
          onChange={(event) => onPickAll(event.target.checked)}
        />
        모두 선택
      </label>
      {asking ? (
        <>
          <span className="text-[var(--color-ink-body)]">
            {picked}개를 삭제할까요?
          </span>
          <Button
            tone="quiet"
            size="compact"
            loading={busy}
            onClick={() => {
              setAsking(false);
              onDelete();
            }}
          >
            삭제
          </Button>
          <Button tone="text" size="compact" onClick={() => setAsking(false)}>
            취소
          </Button>
        </>
      ) : (
        <>
          <Button
            tone="primary"
            size="compact"
            loading={busy}
            disabled={picked === 0}
            onClick={onConfirm}
          >
            선택 확정 ({picked})
          </Button>
          <Button
            tone="quiet"
            size="compact"
            disabled={picked === 0 || busy}
            onClick={() => setAsking(true)}
          >
            선택 삭제
          </Button>
        </>
      )}
    </div>
  );
}
