"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Wordmark } from "@/shared/ui";

import { getMeeting } from "../api";
import { timecode } from "../format";
import type { RecordingState } from "../types";

/**
 * S13's own top bar. The live screen is drawn without the app's sidebar — a
 * meeting in progress is the only thing on screen — so this bar is its whole
 * frame: the wordmark (the way out), the meeting's title, and the REC timer.
 *
 * The controls stay in the rail. The design file repeats pause and stop up
 * here too, but two copies of "녹음 종료" would put two primaries on one screen
 * (ui-spec.md section 0), and the rail is where the timer they act on is.
 *
 * The title is fetched once. `useMeeting` would poll every three seconds for
 * as long as the meeting is `recording`, which is the whole time this bar is
 * on screen, to learn nothing new.
 */
export function LiveTopBar({
  meetingId,
  state,
  elapsedSeconds,
}: {
  meetingId: string;
  /** Null before recording starts: no REC indicator yet. */
  state: RecordingState | null;
  elapsedSeconds: number;
}) {
  const title = useMeetingTitle(meetingId);

  return (
    <header
      className="flex flex-none items-center border-b border-[var(--color-hairline)] bg-[var(--color-surface-panel)]"
      style={{ height: "var(--space-topbar)", padding: "0 var(--space-24)", gap: 14 }}
    >
      <Link href="/" className="text-[var(--color-ink-strong)]">
        <Wordmark height={18} />
      </Link>
      <span aria-hidden style={{ width: 1, height: 20, background: "var(--color-hairline)" }} />
      <div className="min-w-0">
        <div
          className="truncate text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-heading)", fontWeight: "var(--text-heading-weight)" }}
        >
          {title ?? "회의"}
        </div>
        <div className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
          웹 마이크
        </div>
      </div>

      <div className="flex-1" />

      {state === "recording" && (
        <span
          className="flex items-center tabular-nums"
          style={{
            gap: 8,
            fontFamily: "var(--font-mono)",
            fontSize: "var(--control-text-default)",
            fontWeight: 600,
            color: "var(--color-signal-critical)",
          }}
        >
          <span
            aria-hidden
            className="animate-[pulse_3s_ease-in-out_infinite] rounded-full motion-reduce:animate-none"
            style={{ width: 8, height: 8, background: "var(--color-signal-critical)" }}
          />
          REC {timecode(elapsedSeconds)}
        </span>
      )}
      {state === "paused" && (
        <span
          className="tabular-nums text-[var(--color-ink-muted)]"
          style={{ fontFamily: "var(--font-mono)", fontSize: "var(--control-text-default)", fontWeight: 600 }}
        >
          일시정지 {timecode(elapsedSeconds)}
        </span>
      )}
    </header>
  );
}

function useMeetingTitle(meetingId: string): string | null {
  const [title, setTitle] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    getMeeting(meetingId)
      .then((meeting) => {
        if (current) setTitle(meeting.title);
      })
      // A missing title is not worth an error on a screen that is recording;
      // the bar falls back to "회의".
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [meetingId]);
  return title;
}
