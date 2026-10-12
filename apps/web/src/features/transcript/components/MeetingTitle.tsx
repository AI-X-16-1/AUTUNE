"use client";

import { useState, type CSSProperties, type FormEvent } from "react";

import { Button } from "@/shared/ui";

import { renameMeeting } from "../api";
import { renameRefusal } from "../titleRefusal";

/** Matches `MeetingRename.title` in `modules/audio/src/autune_audio/schemas.py`. */
export const MAX_TITLE_CHARS = 400;

export const TITLE_STYLE: CSSProperties = {
  fontSize: "var(--text-title)",
  fontWeight: "var(--text-title-weight)",
  letterSpacing: "var(--text-title-tracking)",
};

const meta: CSSProperties = { fontSize: "var(--text-meta)" };
const INPUT =
  "min-w-0 flex-1 rounded-[var(--radius)] bg-[var(--color-surface-panel)] px-3 text-[var(--color-ink-strong)] focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)]";
const INPUT_STYLE: CSSProperties = {
  height: "var(--control-h-default)",
  fontSize: "var(--text-rowBody)",
  border: "1px solid var(--color-hairline)",
};

/**
 * A meeting's title, and the way to change it (#1161).
 *
 * Any member of the meeting's team may rename it; the server decides, and a
 * refusal is shown as a sentence under the field with what was typed still in
 * it. A title that reads as personal data is refused there and never stored
 * (`titleRefusal`).
 *
 * **What a rename does not reach** is said before it is saved: every message
 * and document Autune sends reads the title at that moment, so only what is
 * sent from now on carries the new one. The server tells no module and sends
 * nothing for a rename.
 *
 * The new title is handed up rather than read back: the route answers with the
 * meeting's id and state and does not repeat a title.
 */
export function MeetingTitle({
  meetingId,
  title,
  onRenamed,
}: {
  meetingId: string;
  title: string;
  onRenamed: (title: string) => void;
}) {
  const [typed, setTyped] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (typed === null) {
    return (
      <div className="flex flex-wrap items-baseline gap-x-3">
        <h1 className="text-ink-strong break-words" style={TITLE_STYLE}>
          {title}
        </h1>
        <Button
          tone="text"
          size="compact"
          type="button"
          onClick={() => setTyped(title)}
        >
          이름 변경
        </Button>
      </div>
    );
  }

  const next = typed.trim();
  const close = () => {
    setTyped(null);
    setError(null);
  };

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || !next) return;
    if (next === title) {
      close();
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await renameMeeting(meetingId, next);
      onRenamed(next);
      close();
    } catch (cause: unknown) {
      setError(renameRefusal(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form aria-label="회의 이름 변경" onSubmit={save}>
      <h1 className="sr-only">{title}</h1>
      <div className="flex flex-wrap items-center gap-2">
        <input
          aria-label="회의 이름"
          type="text"
          className={INPUT}
          style={INPUT_STYLE}
          value={typed}
          maxLength={MAX_TITLE_CHARS}
          autoComplete="off"
          autoFocus
          onChange={(event) => {
            setTyped(event.target.value);
            setError(null);
          }}
        />
        <Button tone="primary" size="compact" type="submit" loading={busy} disabled={!next}>
          저장
        </Button>
        <Button tone="quiet" size="compact" type="button" disabled={busy} onClick={close}>
          취소
        </Button>
      </div>
      <p className="mt-2 text-[var(--color-ink-muted)]" style={meta}>
        이미 보낸 알림과 내보낸 문서에는 이전 이름이 그대로 남습니다.
      </p>
      {error ? (
        <p
          role="alert"
          className="mt-1"
          style={{ ...meta, color: "var(--color-signal-attention)" }}
        >
          {error}
        </p>
      ) : null}
    </form>
  );
}
