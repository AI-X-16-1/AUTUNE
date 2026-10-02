"use client";

import { useState } from "react";

import { Button } from "@/shared/ui";

import { minutesText, titleOf } from "../minutes";
import type { MeetingSummary } from "../types";

/**
 * "회의록 복사" on the summary tab: the meeting's decisions and action items
 * as one page of plain text on the clipboard (`minutes.ts`).
 *
 * Says what it leaves out -- no quotation is in it -- because the person
 * pasting it somewhere should not have to wonder. Where the browser will not
 * give the clipboard, the text is shown to select by hand instead of the
 * button just failing.
 */
export function CopyMinutes({ summary }: { summary: MeetingSummary }) {
  const [state, setState] = useState<"idle" | "copied" | "manual">("idle");
  const text = minutesText(summary, titleOf(summary));

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("manual");
    }
  };

  const meta = { fontSize: "var(--text-metaSmall)" } as const;

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button tone="secondary" size="compact" onClick={() => void copy()}>
          회의록 복사
        </Button>
        <span className="text-[var(--color-ink-muted)]" style={meta}>
          결정과 액션만 담습니다. 근거 발화 인용은 넣지 않습니다.
        </span>
        {state === "copied" ? (
          <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>
            복사했습니다.
          </span>
        ) : null}
      </div>
      {state === "manual" ? (
        <>
          <p role="alert" className="text-[var(--color-ink-muted)]" style={meta}>
            복사하지 못했습니다. 아래 글을 직접 선택해 복사해 주세요.
          </p>
          <textarea
            readOnly
            aria-label="회의록"
            value={text}
            rows={Math.min(16, text.split("\n").length + 1)}
            onFocus={(event) => event.target.select()}
            className="w-full rounded-[var(--radius)] bg-[var(--color-surface-panel)] p-2 text-[var(--color-ink-body)]"
            style={{ ...meta, border: "1px solid var(--color-hairline)" }}
          />
        </>
      ) : null}
    </div>
  );
}
