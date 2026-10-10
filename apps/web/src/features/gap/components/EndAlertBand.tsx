"use client";

import { useEffect, useState } from "react";

import { Band, Button, MaskedText } from "@/shared/ui";

import { END_ALERT, END_ALERT_SHOWN } from "../endAlert";
import type { TeamGap } from "../types";

/**
 * S14, the small cut (#1147): the band a live recording shows five minutes
 * before its planned end.
 *
 * It lists what the team's earlier meetings left open, so the people still in
 * the room can settle it before they leave. It does **not** find what this
 * meeting has left undecided — nothing reads a meeting while it is recorded —
 * and S14's "질문으로 띄우기" is left out with it: there is no question about
 * this meeting to raise. What it reads and the sentence it says are one seam
 * (`endAlert`).
 *
 * This component does not know the time. The live screen (module A's) decides
 * when it is five minutes to the end and mounts whatever the page put in its
 * slot; mounting is the alert, so the list is read once, then. The meeting
 * being recorded is left out of its own "earlier meetings".
 *
 * **No people, as on the team list** (`TeamGapList`): a row is a gap and the
 * meeting it came from. Nothing is sent, stored or logged — the band is drawn
 * in the tab that is recording and is gone when it is closed.
 */
export function EndAlertBand({
  teamId,
  exceptMeetingId,
}: {
  teamId: string;
  exceptMeetingId?: string;
}) {
  const [gaps, setGaps] = useState<TeamGap[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const [closed, setClosed] = useState(false);

  useEffect(() => {
    let current = true;
    END_ALERT.list(teamId)
      .then((list) => {
        if (current) setGaps(list.filter((gap) => gap.meeting_id !== exceptMeetingId));
      })
      .catch(() => {
        if (current) setFailed(true);
      });
    return () => {
      current = false;
    };
  }, [teamId, exceptMeetingId]);

  if (closed) return null;
  // The alert was asked for, so a list that cannot be read is said, quietly:
  // a recording is under way and this is not its error.
  if (failed) return <Quiet>종료 5분 전 · 미해결 갭을 불러오지 못했습니다</Quiet>;
  if (gaps === null) return null;
  if (gaps.length === 0) return <Quiet>종료 5분 전 · {END_ALERT.none}</Quiet>;

  const shown = gaps.slice(0, END_ALERT_SHOWN);
  return (
    <section
      role="status"
      aria-label="종료 5분 전 알림"
      style={{ padding: "var(--space-12) var(--space-24) 0" }}
    >
      <Band
        action={
          <span className="flex items-center" style={{ gap: "var(--space-4)" }}>
            <Button
              tone="secondary"
              size="compact"
              aria-expanded={open}
              onClick={() => setOpen((value) => !value)}
            >
              {open ? "접기" : "보기"}
            </Button>
            <Button tone="secondary" size="compact" onClick={() => setClosed(true)}>
              닫기
            </Button>
          </span>
        }
      >
        종료 5분 전 · {END_ALERT.sentence(gaps.length)}
      </Band>
      {open ? (
        <>
          <ul className="border-b border-[var(--color-hairline)]">
            {shown.map((gap) => (
              <li
                key={gap.gap_id}
                className="border-t border-[var(--color-hairline)]"
                style={{ padding: "var(--space-8) var(--space-4)" }}
              >
                <span
                  className="block text-[var(--color-ink-strong)]"
                  style={{ fontSize: "var(--text-rowBody)" }}
                >
                  <MaskedText>{gap.title}</MaskedText>
                </span>
                <span
                  className="block text-[var(--color-ink-muted)]"
                  style={{ fontSize: "var(--text-metaSmall)" }}
                >
                  {gap.meeting_title} · {gap.meeting_date.slice(0, 10)}
                </span>
              </li>
            ))}
          </ul>
          <p
            className="text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-metaSmall)", paddingTop: "var(--space-4)" }}
          >
            {gaps.length > shown.length ? `외 ${gaps.length - shown.length}건 · ` : ""}
            {/* A new tab: leaving this one would drop the recording. */}
            <a href="/gaps" target="_blank" rel="noreferrer" className="hover:underline">
              갭 리포트를 새 탭에서 열기
            </a>
          </p>
        </>
      ) : null}
    </section>
  );
}

function Quiet({ children }: { children: React.ReactNode }) {
  return (
    <p
      role="status"
      className="text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-meta)", padding: "var(--space-12) var(--space-24) 0" }}
    >
      {children}
    </p>
  );
}
