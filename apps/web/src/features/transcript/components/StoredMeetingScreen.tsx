"use client";

import Link from "next/link";

import { useMeeting } from "../hooks/useMeeting";
import { STATUS_LABEL } from "../status";
import { ProcessingStages } from "./ProcessingStages";
import { StoredTranscript } from "./StoredTranscript";

/**
 * One meeting, from the moment it is queued to its transcript: the container
 * the route mounts.
 *
 * Two screens share this URL and the meeting's status says which one: S12
 * (the pipeline, while `analyzing` or `failed`) and the transcript tab of S15
 * (once `complete`). The other tabs belong to other features, which this one
 * may not import (`CLAUDE.md` in this folder). They are no longer unreachable
 * because of it: the route group's layout draws the tab bar and mounts each
 * feature's own screen behind its own URL (#423), which is the answer #239
 * was asking for — a page composes features, a feature does not.
 *
 * It brings no width or side padding of its own. That layout is the container,
 * and a second one here would centre this column inside it while the action
 * board and the gap report line up with the tab bar.
 *
 * `useMeeting` polls while the pipeline is moving and stops when it is not.
 * `StoredTranscript` is mounted only once the meeting is past `analyzing`, so
 * its one fetch lands after the utterances were written — the "아직 없습니다"
 * it used to show forever was a fetch that landed too early with nothing to
 * tell it to try again.
 */
export function StoredMeetingScreen({ meetingId }: { meetingId: string }) {
  const state = useMeeting(meetingId);

  return (
    <main className="max-w-[720px] pb-[var(--space-page)]">
      <header>
        <p
          className="text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-meta)" }}
        >
          회의
        </p>
        <h1
          className="text-ink-strong"
          style={{
            fontSize: "var(--text-title)",
            fontWeight: "var(--text-title-weight)",
            letterSpacing: "var(--text-title-tracking)",
          }}
        >
          {state.status === "ready" ? state.meeting.title : "회의 전사"}
        </h1>
        <p
          className="mt-2 text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-meta)" }}
        >
          <span>{meetingId}</span>
          {state.status === "ready" ? (
            <>
              <span aria-hidden="true"> · </span>
              <span>
                {STATUS_LABEL[state.meeting.status] ?? state.meeting.status}
              </span>
            </>
          ) : null}
          <span aria-hidden="true"> · </span>
          <Link href={`/meetings/${meetingId}/live`} className="underline">
            실시간 보기
          </Link>
        </p>
      </header>

      <div className="mt-6">{body(state, meetingId)}</div>
    </main>
  );
}

function body(state: ReturnType<typeof useMeeting>, meetingId: string) {
  if (state.status === "loading") {
    return (
      <p
        className="text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-meta)" }}
      >
        회의 정보를 불러오는 중입니다…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <p
        role="alert"
        style={{
          fontSize: "var(--text-meta)",
          color: "var(--color-signal-attention)",
        }}
      >
        {state.message}
      </p>
    );
  }

  const { meeting } = state;
  switch (meeting.status) {
    case "scheduled":
      return (
        <p
          className="text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-meta)" }}
        >
          아직 녹음이 없습니다.{" "}
          <Link
            href={`/meetings/new?meeting=${meetingId}`}
            className="text-[var(--color-accent-default)]"
          >
            녹음 파일 올리기
          </Link>
        </p>
      );
    case "recording":
    case "analyzing":
    case "failed":
      return <ProcessingStages meeting={meeting} />;
    default:
      return (
        <div className="border-t border-[var(--color-hairline)]">
          <StoredTranscript meetingId={meetingId} />
        </div>
      );
  }
}
