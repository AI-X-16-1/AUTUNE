"use client";

import type { Route } from "next";
import Link from "next/link";
import { useEffect, useId, useState } from "react";

import { Button } from "@/shared/ui";

import { CopyMinutes, pageOf } from "./CopyMinutes";
import { ProjectGroups } from "./ProjectGroups";
import { getSummary, putSummaryNote } from "../api";
import { WRITTEN_BY_MODEL, actionMeta, minutesOf } from "../minutes";
import { ALL_PROJECTS, type ProjectChoice } from "../projectFilter";
import { MAX_NOTE_CHARS, UNCONFIRMED_DECISION } from "../types";
import type { MeetingSummary } from "../types";

/**
 * The gutter under S15's tab row. The review layout gives none (#534): each tab
 * brings its own, and the route files that wrap the transcript and context tabs
 * use this same value, so the first line of every tab sits in one place.
 */
const TAB_BODY = { padding: "20px var(--space-page) var(--space-page)" } as const;

/**
 * S15's 요약 tab: the meeting's minutes, as one document (the user,
 * 2026-10-09; #421 was the tab's first shape).
 *
 * The tab used to be a status page -- counts, then two lists with a dot per
 * row and the line each row came from -- with a button that copied a
 * different, shorter page. It is now the page somebody would paste: a title
 * and the day, the decisions numbered, the items with who and by when, the
 * team's memo. `minutes.ts` decides what is on it, and "회의록 복사" copies
 * exactly that, so what is read here is what leaves.
 *
 * Nothing on it was written by a model for this page: the sentences are the
 * ones the review already shows. The one exception is the summary paragraph a
 * cloud model wrote, only where the deployment turned it on (#392), and it
 * says so. A decision nobody confirmed says it was extracted; an item waiting
 * for confirmation says that.
 *
 * Under the document, and not part of it: what the page leaves out (candidate
 * items, questions left open, agreements still waiting for their speaker) with
 * the way to the 액션 tab, and -- for a team that lists projects -- the tool
 * that sorts rows into them (`ProjectGroups`, 2026-10-04).
 */
export function MeetingSummaryScreen({ meetingId }: { meetingId: string }) {
  const [summary, setSummary] = useState<MeetingSummary | null>(null);
  const [failed, setFailed] = useState(false);
  const [project, setProject] = useState<ProjectChoice>(ALL_PROJECTS);
  const heading = useId();

  useEffect(() => {
    let alive = true;
    getSummary(meetingId)
      .then((answer) => alive && setSummary(answer))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [meetingId]);

  if (summary === null) {
    return (
      <Note>
        {failed ? "회의록을 불러오지 못했습니다." : "회의록을 불러오는 중입니다."}
      </Note>
    );
  }

  const actionsTab = `/meetings/${meetingId}/actions` as Route;
  const shown = pageOf(summary, project);
  const page = minutesOf(shown.summary, shown.title);
  const left = [
    page.candidates > 0 ? `후보 ${page.candidates}건` : null,
    summary.open_questions > 0 ? `미결 질문 ${summary.open_questions}건` : null,
    summary.ambiguous_waiting > 0
      ? `응답 대기 모호 동의 ${summary.ambiguous_waiting}건`
      : null,
  ].filter((part) => part !== null);

  return (
    <main className="flex max-w-[860px] flex-col gap-4" style={TAB_BODY}>
      <CopyMinutes summary={summary} project={project} onProject={setProject} />

      <article
        aria-labelledby={heading}
        className="flex flex-col gap-7 border border-[var(--color-hairline)] bg-[var(--color-surface-panel)]"
        style={{ borderRadius: "var(--radius)", padding: "var(--space-page)" }}
      >
        <header>
          <h1
            id={heading}
            className="text-[var(--color-ink-strong)]"
            style={{
              fontSize: "var(--text-title)",
              fontWeight: "var(--text-title-weight)",
              lineHeight: "var(--text-title-leading)",
              letterSpacing: "var(--text-title-tracking)",
            }}
          >
            {page.title}
          </h1>
          {page.day ? (
            <p className="mt-1">
              <Meta>{page.day}</Meta>
            </p>
          ) : null}
        </header>

        {page.overview ? (
          <section aria-label="요약">
            <Heading aside={WRITTEN_BY_MODEL}>요약</Heading>
            <Body>{page.overview.text}</Body>
            {page.overview.points.length > 0 ? (
              <ul className="mt-2 list-disc pl-5">
                {page.overview.points.map((point, n) => (
                  <Entry key={n}>{point}</Entry>
                ))}
              </ul>
            ) : null}
            <p className="mt-2">
              <Meta>
                모델이 회의 발화로 쓴 요약입니다. 아래 결정·액션과 다를 수
                있습니다.
              </Meta>
            </p>
          </section>
        ) : null}

        <section aria-label="결정 사항">
          <Heading>결정 사항</Heading>
          {page.decisions.length === 0 ? (
            <Note>없음</Note>
          ) : (
            <ol className="list-decimal pl-5">
              {page.decisions.map((decision) => (
                <Entry key={decision.id}>
                  {decision.statement}
                  {decision.unconfirmed ? (
                    <Meta> ({UNCONFIRMED_DECISION})</Meta>
                  ) : null}
                </Entry>
              ))}
            </ol>
          )}
        </section>

        <section aria-label="액션">
          <Heading>액션</Heading>
          {page.actions.length === 0 ? (
            <Note>없음</Note>
          ) : (
            <ol className="list-decimal pl-5">
              {page.actions.map((item) => (
                <Entry key={item.id}>
                  {item.description}
                  <span
                    className={
                      item.overdue
                        ? "text-[var(--color-signal-critical)]"
                        : "text-[var(--color-ink-muted)]"
                    }
                    style={{ fontSize: "var(--text-meta)" }}
                  >
                    {" — "}
                    {actionMeta(item)}
                    {item.overdue ? " · 기한 지남" : null}
                  </span>
                </Entry>
              ))}
            </ol>
          )}
        </section>

        <MemoEditor
          meetingId={meetingId}
          summary={summary}
          onSaved={(answer) => setSummary(answer)}
        />
      </article>

      <section
        aria-label="회의록에 없는 것"
        className="flex flex-col gap-1"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        {left.length > 0 ? (
          <Meta>{left.join(" · ")}은 회의록에 넣지 않았습니다.</Meta>
        ) : null}
        {!page.overview && summary.generated_too_long ? (
          <Meta>회의가 길어 AI 요약을 만들지 못했습니다.</Meta>
        ) : null}
        <Link href={actionsTab} className="text-[var(--color-accent-default)]">
          근거 발화와 수정은 액션 탭에서 →
        </Link>
      </section>

      {summary.projects && summary.projects.length > 0 ? (
        <ProjectGroups
          meetingId={meetingId}
          summary={summary}
          onChange={setSummary}
        />
      ) : null}
    </main>
  );
}

function MemoEditor({
  meetingId,
  summary,
  onSaved,
}: {
  meetingId: string;
  summary: MeetingSummary;
  onSaved: (answer: MeetingSummary) => void;
}) {
  const [draft, setDraft] = useState(summary.note ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(false);
  const changed = draft.trim() !== (summary.note ?? "");

  const save = async () => {
    setSaving(true);
    setError(false);
    try {
      const answer = await putSummaryNote(meetingId, draft);
      setDraft(answer.note ?? "");
      onSaved(answer);
    } catch {
      setError(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section aria-label="메모">
      <Heading>메모</Heading>
      <textarea
        aria-label="메모"
        value={draft}
        maxLength={MAX_NOTE_CHARS}
        onChange={(event) => setDraft(event.target.value)}
        rows={4}
        placeholder="회의에 대해 팀이 남길 말을 적어 주세요. 비워 두고 저장하면 지워집니다."
        className="w-full border border-[var(--color-hairline)] text-[var(--color-ink-body)]"
        style={{
          borderRadius: "var(--radius)",
          padding: "var(--space-row)",
          fontSize: "var(--text-body)",
          lineHeight: "var(--text-body-leading)",
          background: "var(--color-surface-paper)",
        }}
      />
      <div
        className="mt-2 flex items-center gap-3"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        <span className="flex-1 text-[var(--color-ink-muted)]">
          {error
            ? "저장하지 못했습니다. 다시 시도해 주세요."
            : changed
              ? "저장해야 회의록 복사에 들어갑니다."
              : summary.note_updated_at
                ? `마지막 저장 ${localTime(summary.note_updated_at)}`
                : `${draft.length}/${MAX_NOTE_CHARS}`}
        </span>
        <Button
          tone="primary"
          size="compact"
          onClick={save}
          loading={saving}
          disabled={!changed}
        >
          저장
        </Button>
      </div>
    </section>
  );
}

/**
 * When the memo was saved, in the viewer's own time zone, as YYYY-MM-DD HH:MM.
 * The server sends UTC; cutting its string showed Seoul's 18:10 as 09:10
 * (found in a browser check). `sv-SE` is the locale that reads that way.
 */
function localTime(iso: string): string {
  return new Date(iso).toLocaleString("sv-SE").slice(0, 16);
}

/** One numbered or bulleted line of the document. */
function Entry({ children }: { children: React.ReactNode }) {
  return (
    <li
      className="py-0.5 text-[var(--color-ink-strong)]"
      style={{
        fontSize: "var(--text-body)",
        lineHeight: "var(--text-body-leading)",
      }}
    >
      {children}
    </li>
  );
}

function Body({ children }: { children: string }) {
  return (
    <p
      className="text-[var(--color-ink-strong)]"
      style={{
        fontSize: "var(--text-body)",
        lineHeight: "var(--text-body-leading)",
      }}
    >
      {children}
    </p>
  );
}

function Heading({ children, aside }: { children: string; aside?: string }) {
  return (
    <h2
      className="mb-2 border-b border-[var(--color-hairline)] pb-1 text-[var(--color-ink-strong)]"
      style={{
        fontSize: "var(--text-heading)",
        fontWeight: "var(--text-heading-weight)",
        lineHeight: "var(--text-heading-leading)",
      }}
    >
      {children}
      {aside ? <Meta> · {aside}</Meta> : null}
    </h2>
  );
}

function Meta({ children }: { children: React.ReactNode }) {
  return (
    <span
      className="text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-metaSmall)", fontWeight: "normal" }}
    >
      {children}
    </span>
  );
}

function Note({ children }: { children: string }) {
  return (
    <p
      className="text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-meta)" }}
    >
      {children}
    </p>
  );
}
