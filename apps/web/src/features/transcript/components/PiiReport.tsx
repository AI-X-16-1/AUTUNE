"use client";

import { useEffect, useState } from "react";

import { Button, ChipToggle } from "@/shared/ui";

import { reportPiiMiss } from "../api";
import type { PiiCategory, PiiReported } from "../types";

/**
 * S30 — 개인정보 누락 신고 (#555).
 *
 * Select text in a stored transcript → a floating "개인정보 신고" text button
 * → this modal → the span is masked on the server and B, C and D are sent the
 * corrected transcript.
 *
 * **Offsets are what leave the browser, never the selected text.** The modal
 * shows the selection — the reader already has it on screen — but the request
 * carries only where it starts and ends in the utterance (`reportPiiMiss`).
 *
 * "같은 형태를 워크스페이스 마스킹 규칙에 추가" stores the span's *shape*
 * (`A-#####`), not the span, and only when it carries a digit — a rule built
 * from a name would mask every word of that length (`masking_rules.py`). The
 * DM to the reporter is left out: the result is shown on this screen.
 */

export type Selected = {
  utteranceId: string;
  start: number;
  end: number;
  text: string;
  /** Where the floating button goes: just under the selection, in viewport px. */
  top: number;
  left: number;
};

const CATEGORIES: { value: PiiCategory; label: string }[] = [
  { value: "name", label: "이름" },
  { value: "internal_id", label: "사내 ID" },
  { value: "contact", label: "연락처" },
  { value: "other", label: "기타" },
];

/**
 * The selection inside one utterance's text, as offsets into that text.
 *
 * `MaskedText` renders the stored string unchanged — a redaction is the same
 * characters wrapped in a span — so the length of the text before the
 * selection is its offset in `utterance.text`. A selection that crosses two
 * rows is not a report about one utterance and is ignored.
 */
export function readSelection(): Selected | null {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0)
    return null;
  const range = selection.getRangeAt(0);
  const owner = (node: Node | null) =>
    (node instanceof Element
      ? node
      : node?.parentElement
    )?.closest<HTMLElement>("[data-utterance-id]") ?? null;
  const row = owner(range.startContainer);
  if (!row || row !== owner(range.endContainer)) return null;

  const before = document.createRange();
  before.setStart(row, 0);
  before.setEnd(range.startContainer, range.startOffset);
  const start = before.toString().length;
  const text = range.toString();
  if (!text.trim()) return null;

  const rect = range.getBoundingClientRect();
  return {
    utteranceId: row.dataset.utteranceId ?? "",
    start,
    end: start + text.length,
    text,
    top: rect.bottom + 6,
    left: rect.left,
  };
}

export function ReportButton({
  selected,
  onOpen,
}: {
  selected: Selected;
  onOpen: () => void;
}) {
  return (
    <div
      className="fixed z-40"
      style={{ top: selected.top, left: selected.left }}
    >
      <div
        style={{
          background: "var(--color-surface-panel)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--shadow-overlay)",
        }}
      >
        {/* mousedown, not click: a click would first clear the selection it is about. */}
        <Button
          tone="text"
          size="compact"
          onMouseDown={(event) => {
            event.preventDefault();
            onOpen();
          }}
        >
          개인정보 신고
        </Button>
      </div>
    </div>
  );
}

export function PiiReportModal({
  meetingId,
  selected,
  context,
  onClose,
  onReported,
}: {
  meetingId: string;
  selected: Selected;
  /** The whole utterance, so the selection can be shown where it sits. */
  context: { time: string; speaker: string; text: string };
  onClose: () => void;
  onReported: (result: PiiReported) => void;
}) {
  const [category, setCategory] = useState<PiiCategory | null>(null);
  const [similar, setSimilar] = useState(true);
  const [addRule, setAddRule] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pending) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, pending]);

  const submit = async () => {
    if (!category) return;
    setPending(true);
    setError(null);
    try {
      onReported(
        await reportPiiMiss(meetingId, selected.utteranceId, {
          start: selected.start,
          end: selected.end,
          category,
          include_similar: similar,
          add_rule: addRule,
        }),
      );
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "신고하지 못했습니다.",
      );
      setPending(false);
    }
  };

  const { text } = context;
  return (
    <div
      role="dialog"
      aria-modal
      aria-label="개인정보 신고"
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(22,25,31,.35)" }}
      onClick={pending ? undefined : onClose}
    >
      <div
        className="w-full max-w-[480px]"
        style={{
          background: "var(--color-surface-panel)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--shadow-overlay)",
          padding: "var(--space-card)",
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2
            className="text-[var(--color-ink-strong)]"
            style={{
              fontSize: "var(--text-title)",
              fontWeight: "var(--text-title-weight)",
            }}
          >
            개인정보 신고
          </h2>
          <Button
            tone="quiet"
            size="compact"
            onClick={onClose}
            disabled={pending}
            aria-label="닫기"
          >
            ×
          </Button>
        </div>

        <div
          className="mt-3"
          style={{
            fontSize: "var(--text-metaSmall)",
            color: "var(--color-ink-muted)",
          }}
        >
          {context.time} · {context.speaker}
        </div>
        <p
          className="mt-1 text-[var(--color-ink-body)]"
          style={{
            fontSize: "var(--text-body)",
            lineHeight: "var(--text-body-leading)",
          }}
        >
          {text.slice(0, selected.start)}
          <mark
            style={{
              background: "var(--color-accent-selection)",
              color: "var(--color-ink-strong)",
            }}
          >
            {text.slice(selected.start, selected.end)}
          </mark>
          {text.slice(selected.end)}
        </p>

        <div
          className="mt-4"
          style={{
            fontSize: "var(--text-label)",
            fontWeight: "var(--text-label-weight)",
          }}
        >
          유형
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {CATEGORIES.map((option) => (
            <ChipToggle
              key={option.value}
              selected={category === option.value}
              onClick={() => setCategory(option.value)}
            >
              {option.label}
            </ChipToggle>
          ))}
        </div>

        <ul
          className="mt-4 flex flex-col gap-2"
          style={{ fontSize: "var(--text-meta)" }}
        >
          <li className="text-[var(--color-ink-body)]">
            ✓ 선택 구간을 즉시 마스킹 · 요약·액션·갭 분석에 다시 반영
          </li>
          <li>
            <label className="flex items-center gap-2 text-[var(--color-ink-body)]">
              <input
                type="checkbox"
                checked={similar}
                onChange={(event) => setSimilar(event.target.checked)}
              />
              이 회의의 같은 구간도 모두 마스킹
            </label>
          </li>
          <li>
            <label className="flex items-center gap-2 text-[var(--color-ink-body)]">
              <input
                type="checkbox"
                checked={addRule}
                onChange={(event) => setAddRule(event.target.checked)}
              />
              같은 형태를 워크스페이스 마스킹 규칙에 추가 · 숫자가 든 사번·ID 형태만
            </label>
          </li>
        </ul>

        <p
          className="mt-3"
          style={{
            fontSize: "var(--text-metaSmall)",
            color: "var(--color-ink-muted)",
          }}
        >
          신고에는 위치와 유형만 전송되며 원문은 저장되지 않습니다.
        </p>

        {error && (
          <p
            role="alert"
            className="mt-2"
            style={{
              fontSize: "var(--text-meta)",
              color: "var(--color-signal-critical)",
            }}
          >
            {error}
          </p>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <Button tone="quiet" onClick={onClose} disabled={pending}>
            취소
          </Button>
          <Button
            tone="primary"
            onClick={() => void submit()}
            loading={pending}
            disabled={!category}
          >
            신고 · 마스킹
          </Button>
        </div>
      </div>
    </div>
  );
}
