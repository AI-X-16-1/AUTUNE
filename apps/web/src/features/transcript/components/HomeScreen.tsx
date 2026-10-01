"use client";

import Link from "next/link";

import { Row } from "@/shared/ui/Row";
import { StatusDot } from "@/shared/ui/StatusDot";

import { meetingDate } from "../format";
import { useMeetings, type MeetingsState } from "../hooks/useMeetings";
import { STATUS_DOT, STATUS_LABEL, isBeingRecorded } from "../status";
import type { MeetingSummary } from "../types";

/**
 * S05, the front door — every meeting this person can see, newest first.
 *
 * It replaces a placeholder that rendered three hardcoded rows for meetings
 * that did not exist, with no links and no fetch. The product worked end to
 * end and there was no way to reach any of it without typing a meeting id into
 * the URL bar; this is that way.
 *
 * **Four states, and the empty one is the one that matters.** A new install has
 * no meetings, so "아직 회의가 없습니다" with a way to make the first one is the
 * difference between a working product and a broken-looking one — and it is the
 * state a reviewer sees first. `useMeetings` returns an empty list as `ready`
 * rather than as an error precisely so this screen can say it.
 *
 * **What a row shows is what module A knows.** Status, title and start time.
 * Not an utterance count, which is one join from a per-person speech volume
 * (privacy.md section 3); not action items or gaps, which are modules B's and
 * C's and which A may not read (invariant 2). The row's job is to say what
 * happened to the meeting and to link to the screens that own the rest — which
 * is also why the *whole row* is the link rather than a button in its action
 * slot: one link per row, nothing nested inside it, and the target is the
 * screen that decides what to draw.
 *
 * S05 in the spec is more than this: a next-meeting block, "things for me",
 * unresolved gaps, retention countdowns. Three of those four are other
 * features' data and #239 is the open question about how one page composes
 * features. What is here is the list, which is what makes the rest reachable.
 */
export function HomeScreen() {
  const state = useMeetings();

  return (
    // S05's content column: the page gutter from the panel's left edge, not
    // centred. The shell's top bar already says "홈" and the sidebar holds the
    // one primary action ("회의 시작"), so the screen opens straight on its
    // first section, as S05 does. 720 is the reading width — a list of meeting
    // titles stretched across the panel is a line your eye has to travel back
    // across.
    <main style={{ padding: "var(--space-24) var(--space-page)" }}>
      <section className="max-w-[720px]" aria-labelledby="home-meetings">
        <header className="flex items-baseline gap-2.5" style={{ marginBottom: "var(--space-4)" }}>
          <h1
            id="home-meetings"
            className="text-ink-strong"
            style={{
              fontSize: "var(--text-heading)",
              fontWeight: "var(--text-heading-weight)",
            }}
          >
            최근 회의
          </h1>
          {state.status === "ready" && state.meetings.length > 0 && (
            <span
              className="text-[var(--color-ink-muted)]"
              style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-label)", fontWeight: 500 }}
            >
              {state.meetings.length}
            </span>
          )}
        </header>

        {body(state)}
      </section>
    </main>
  );
}

function body(state: MeetingsState) {
  if (state.status === "loading") {
    return (
      <p
        className="text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-meta)" }}
      >
        회의 목록을 불러오는 중입니다…
      </p>
    );
  }

  if (state.status === "error") {
    return (
      <p
        role="alert"
        style={{
          fontSize: "var(--text-meta)",
          color: "var(--color-signal-critical)",
        }}
      >
        {state.message}
      </p>
    );
  }

  if (state.meetings.length === 0) {
    return (
      <div
        className="rounded-[var(--radius)] border border-dashed border-[var(--color-hairline)] px-4 py-8 text-center"
        style={{ background: "var(--color-surface-sunken)" }}
      >
        <p
          className="text-[var(--color-ink-strong)]"
          style={{
            fontSize: "var(--text-rowTitle)",
            fontWeight: "var(--text-rowTitle-weight)",
          }}
        >
          아직 회의가 없습니다
        </p>
        <p
          className="mt-1 text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          녹음 파일을 올리면 전사 · 화자 분리 · 개인정보 마스킹까지 이어서
          처리됩니다.
        </p>
        <Link
          href="/meetings/new"
          className="mt-3 inline-block text-[var(--color-accent-default)]"
          style={{ fontSize: "var(--text-meta)" }}
        >
          첫 회의 만들기
        </Link>
      </div>
    );
  }

  return (
    <div className="border-t border-[var(--color-hairline)]">
      {state.meetings.map((meeting) => (
        <MeetingRow key={meeting.meeting_id} meeting={meeting} />
      ))}
    </div>
  );
}

/**
 * One meeting, as a link.
 *
 * `Link` around `Row` rather than a button inside its action slot: the whole
 * row is the target, so there is one thing to click and nothing interactive
 * nested inside it. A meeting still being recorded goes to the live view
 * (`isBeingRecorded`), because the stored screen has nothing to draw for it yet.
 */
function MeetingRow({ meeting }: { meeting: MeetingSummary }) {
  const dot = STATUS_DOT[meeting.status];
  const date = meetingDate(meeting.started_at);
  const id = meeting.meeting_id;

  return (
    <Link
      href={
        isBeingRecorded(meeting.status)
          ? `/meetings/${id}/live`
          : `/meetings/${id}`
      }
      className="block focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)]"
    >
      <Row
        dot={<StatusDot variant={dot.variant} hollow={dot.hollow} />}
        title={meeting.title}
        meta={
          <>
            <span>{STATUS_LABEL[meeting.status]}</span>
            <span aria-hidden="true"> · </span>
            {/* A meeting uploaded after the fact has no start time to show. */}
            <span>{date ?? "시작 시각 미기록"}</span>
          </>
        }
      />
    </Link>
  );
}
