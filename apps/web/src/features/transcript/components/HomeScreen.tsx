"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Button } from "@/shared/ui/Button";
import { Row } from "@/shared/ui/Row";
import { StatusDot } from "@/shared/ui/StatusDot";

import { listTeams } from "../api";
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
 * no meetings, so S03's first-meeting screen (`FirstMeeting`) is the
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
  useSendTeamlessToWorkspace();

  // S03 replaces the whole screen, not just the list: with no meetings there
  // is no "최근 회의" to title, and the first upload is the screen's subject.
  if (state.status === "ready" && state.meetings.length === 0) {
    return <FirstMeeting />;
  }

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
            {/* A meeting uploaded after the fact has no start time to show,
                and says so here rather than leaving the date column blank. */}
            {date === null && (
              <>
                <span aria-hidden="true"> · </span>
                <span>시작 시각 미기록</span>
              </>
            )}
          </>
        }
        // S05 puts the date at the row's right edge, short and in mono, so a
        // column of meetings reads down by day. The full time is the title.
        actions={
          date !== null && meeting.started_at ? (
            <time
              dateTime={meeting.started_at}
              title={date}
              className="tabular-nums text-[var(--color-ink-muted)]"
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "var(--text-label)",
                fontWeight: "var(--text-data-weight)",
              }}
            >
              {shortDate(meeting.started_at)}
            </time>
          ) : undefined
        }
      />
    </Link>
  );
}

/**
 * Somebody on no team has nothing to see here and cannot open a meeting, so
 * they go to S02 first. Checked on the home screen because that is where sign-in
 * lands; a failed check leaves them here rather than guessing.
 */
function useSendTeamlessToWorkspace() {
  const router = useRouter();
  useEffect(() => {
    let current = true;
    listTeams()
      .then((teams) => {
        if (current && teams.length === 0) router.replace("/workspace/new");
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [router]);
}

/**
 * `09/04`: month and day of a meeting's start, in the reader's timezone, the
 * way S05 writes the date column. A meeting from another year carries it in
 * front (`2025/09/04`), because a bare `09/04` from last year reads as this
 * year's. Built by hand for the same reason `meetingDate` is: a
 * locale-shaped string can differ between the server's first render and the
 * browser's.
 */
function shortDate(iso: string): string {
  const at = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  const day = `${pad(at.getMonth() + 1)}/${pad(at.getDate())}`;
  return at.getFullYear() === new Date().getFullYear()
    ? day
    : `${at.getFullYear()}/${day}`;
}

/** The formats `NewMeetingScreen` accepts, and the size `MAX_UPLOAD_BYTES`
 * (`modules/audio/src/autune_audio/config.py`) enforces. */
const UPLOAD_LIMITS = "mp3 · wav · m4a · 최대 500MB";

/**
 * S03, the home screen of somebody with no meetings yet.
 *
 * What S03 draws and this does not, and why:
 *
 * - **No name in the heading.** S03 greets "<이름>님". The signed-in user's
 *   name is `/api/auth/me`, and this feature calls only `/api/audio`
 *   (`CLAUDE.md`), which has no "who am I". A heading with somebody else's
 *   placeholder name would be worse than none.
 * - **No checklist.** Slack, voice enrolment and Notion · Jira each have a
 *   done/pending state that lives in another feature or has no endpoint
 *   module A can read. A row that always says "pending" is a row that lies
 *   the day somebody finishes it. The left column instead names the two ways
 *   a meeting gets in, both of which work today.
 * - **No "최대 3h".** Nothing enforces a duration: the server checks the
 *   size while it writes the bytes, and ffmpeg decides what they are. The
 *   dropzone states the limits that are real.
 * - **No drag and drop.** Uploading needs a team, a title and the consent
 *   attestation (S10), which live on `/meetings/new`; a file dropped here
 *   could not be carried there, so "파일 선택" goes to that form instead of
 *   accepting a file it would then lose. The zone is titled for what it does.
 * - **No sample meeting.** There is no sample to open.
 */
function FirstMeeting() {
  const router = useRouter();

  return (
    <main style={{ padding: "var(--space-24) var(--space-page)" }}>
      <h1
        className="text-[var(--color-ink-strong)]"
        style={{
          fontSize: "var(--text-title)",
          fontWeight: "var(--text-title-weight)",
          lineHeight: "var(--text-title-leading)",
          letterSpacing: "var(--text-title-tracking)",
        }}
      >
        첫 회의를 분석해 볼까요?
      </h1>
      <p
        className="text-[var(--color-ink-muted)]"
        style={{
          marginTop: "var(--space-4)",
          fontSize: "var(--text-rowBody)",
          lineHeight: "var(--text-rowBody-leading)",
        }}
      >
        녹음 파일 하나면 시작할 수 있습니다. 전사 · 화자 분리 · 개인정보 마스킹까지
        이어서 처리됩니다.
      </p>

      <div
        className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_var(--layout-miniWindow)]"
        style={{ gap: "var(--space-32)", marginTop: "var(--space-32)" }}
      >
        <section
          aria-label="녹음 파일 올리기"
          className="flex flex-col items-center justify-center text-center md:order-2"
          style={{
            gap: "var(--space-8)",
            padding: "var(--space-48) var(--space-page)",
            borderRadius: "var(--radius)",
            border: "1.5px dashed var(--color-hairline)",
            background:
              "repeating-linear-gradient(135deg, var(--color-surface-paper) 0 var(--space-8), var(--color-surface-panel) var(--space-8) var(--space-16))",
          }}
        >
          <h2
            className="text-[var(--color-ink-strong)]"
            style={{
              fontSize: "var(--text-heading)",
              fontWeight: "var(--text-heading-weight)",
            }}
          >
            녹음 파일 올리기
          </h2>
          <p
            className="text-[var(--color-ink-muted)]"
            style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-label)" }}
          >
            {UPLOAD_LIMITS}
          </p>
          {/* A wrapper for the spacing: `Button` sets its own `style`. */}
          <div style={{ marginTop: "var(--space-8)" }}>
            <Button onClick={() => router.push("/meetings/new")}>파일 선택</Button>
          </div>
          <p
            className="text-[var(--color-ink-muted)]"
            style={{
              marginTop: "var(--space-8)",
              fontSize: "var(--text-metaSmall)",
              lineHeight: "var(--text-metaSmall-leading)",
            }}
          >
            음성 인식 → 화자 분리 → 개인정보 마스킹 순서로 처리되고
            <br />
            원본 음성은 화자 분리가 끝나는 즉시 삭제됩니다
          </p>
        </section>

        <div className="border-t border-[var(--color-hairline)] md:order-1">
          <Row
            title="끝난 회의라면 — 녹음 파일 올리기"
            meta="mp3 · wav · m4a 파일을 올리면 전사부터 분석까지 이어서 처리됩니다."
          />
          <Row
            title="지금 시작하는 회의라면 — 실시간 전사"
            meta="사이드바의 회의 시작에서 마이크로 바로 전사합니다."
          />
        </div>
      </div>
    </main>
  );
}
