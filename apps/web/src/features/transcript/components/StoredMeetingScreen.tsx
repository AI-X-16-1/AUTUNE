import Link from "next/link";

import { StoredTranscript } from "./StoredTranscript";

/**
 * A finished meeting, read back: the container the route mounts.
 *
 * The transcript tab of S15, on its own. S15 draws five tabs — summary,
 * actions, gaps, context, transcript — and four of them belong to other
 * features, which this one may not import (`CLAUDE.md` in this folder; #239 is
 * the open question about how a page composes features). Until that is
 * decided, this screen is the one tab this feature owns, and the route that
 * mounts it is the first URL in the app that shows something the pipeline
 * actually wrote.
 *
 * No title. There is no endpoint that reads a meeting's own row yet — only its
 * transcript — and inventing one for a heading would be a route without a
 * design. The id is shown instead; it is what the URL already says.
 *
 * `StoredTranscript` owns loading, error and empty. This component owns the
 * frame around it, and the one link out: the live view, which is the same
 * meeting seen from the other end of the pipeline.
 */
export function StoredMeetingScreen({ meetingId }: { meetingId: string }) {
  return (
    <main className="mx-auto max-w-[720px] p-[var(--space-page)]">
      <header>
        <h1
          className="text-ink-strong"
          style={{
            fontSize: "var(--text-title)",
            fontWeight: "var(--text-title-weight)",
            letterSpacing: "var(--text-title-tracking)",
          }}
        >
          회의 전사
        </h1>
        <p className="mt-2 text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
          <span>{meetingId}</span>
          <span aria-hidden="true"> · </span>
          <Link href={`/meetings/${meetingId}/live`} className="underline">
            실시간 보기
          </Link>
        </p>
      </header>

      <div className="mt-6 border-t border-hairline">
        <StoredTranscript meetingId={meetingId} />
      </div>
    </main>
  );
}
