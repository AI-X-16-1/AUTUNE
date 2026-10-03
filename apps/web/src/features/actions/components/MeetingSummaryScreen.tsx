"use client";

import type { Route } from "next";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Button, StatusDot } from "@/shared/ui";

import { CopyMinutes } from "./CopyMinutes";
import { getSummary, putSummaryNote } from "../api";
import { isOverdue } from "../dates";
import { COLUMNS, COLUMN_LABELS, MAX_NOTE_CHARS } from "../types";
import type { ActionItemRead, MeetingSummary } from "../types";

/**
 * The gutter under S15's tab row. The review layout gives none (#534): each tab
 * brings its own, and the route files that wrap the transcript and context tabs
 * use this same value, so the first line of every tab sits in one place.
 */
const TAB_BODY = { padding: "20px var(--space-page) var(--space-page)" } as const;

/**
 * S15's 요약 tab, v1 (#421, WBS 4.9): what the meeting settled and left, read
 * top down in three levels — the counts, then the decisions and items
 * themselves, then (on the 액션 tab) the lines each came from — and a memo the
 * team writes.
 *
 * Nothing here was written by a model: the statements and descriptions are the
 * ones the review already shows, and the counts are counts. A prose summary by
 * an LLM is v2 and waits on #392. Pending decisions carry the ochre dot, the
 * spec's mark for something still waiting on a person.
 */
export function MeetingSummaryScreen({ meetingId }: { meetingId: string }) {
  const [summary, setSummary] = useState<MeetingSummary | null>(null);
  const [failed, setFailed] = useState(false);

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
        {failed ? "요약을 불러오지 못했습니다." : "요약을 불러오는 중입니다."}
      </Note>
    );
  }

  const actionsTab = `/meetings/${meetingId}/actions` as Route;
  const confirmed = summary.decisions.filter(
    (d) => d.status === "confirmed",
  ).length;
  const pending = summary.decisions.length - confirmed;
  const byStatus = COLUMNS.map(
    (status) =>
      [
        status,
        summary.action_items.filter((i) => i.status === status).length,
      ] as const,
  );
  const overdue = summary.action_items.filter(
    (i) => isOverdue(i),
  ).length;

  return (
    <main className="flex max-w-[860px] flex-col gap-8" style={TAB_BODY}>
      <section aria-label="개요">
        <Heading>개요</Heading>
        <dl
          className="grid grid-cols-2 gap-x-6 gap-y-2 md:grid-cols-4"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          <Count label="결정" value={`확정 ${confirmed} · 대기 ${pending}`} />
          <Count
            label="액션"
            value={
              byStatus
                .filter(([, n]) => n > 0)
                .map(([status, n]) => `${COLUMN_LABELS[status]} ${n}`)
                .join(" · ") || "없음"
            }
            alert={overdue > 0 ? `기한 지남 ${overdue}` : undefined}
          />
          <Count label="미결 질문" value={`${summary.open_questions}건`} />
          <Count
            label="응답 대기 모호 동의"
            value={`${summary.ambiguous_waiting}건`}
          />
        </dl>
      </section>

      <section aria-label="결정">
        <Heading>결정</Heading>
        {summary.decisions.length === 0 ? (
          <Note>이 회의에서 정리된 결정이 없습니다.</Note>
        ) : (
          <ul>
            {summary.decisions.map((d) => (
              <Line
                key={d.id}
                dot={d.status === "confirmed" ? "confirmed" : "attention"}
              >
                <span className="text-[var(--color-ink-strong)]">
                  {d.statement}
                </span>
                {d.status === "pending" ? <Meta> · 확인 대기</Meta> : null}
              </Line>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="액션">
        <Heading>액션</Heading>
        {summary.action_items.length === 0 ? (
          <Note>이 회의에서 나온 액션 아이템이 없습니다.</Note>
        ) : (
          <ul>
            {summary.action_items.map((item) => (
              <ItemLine key={item.id} item={item} />
            ))}
          </ul>
        )}
        <p className="mt-2" style={{ fontSize: "var(--text-metaSmall)" }}>
          <Link
            href={actionsTab}
            className="text-[var(--color-accent-default)]"
          >
            근거 발화와 수정은 액션 탭에서 →
          </Link>
        </p>
      </section>

      <section aria-label="회의록">
        <Heading>회의록</Heading>
        <CopyMinutes summary={summary} />
      </section>

      <MemoEditor
        meetingId={meetingId}
        summary={summary}
        onSaved={(answer) => setSummary(answer)}
      />
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
    <section aria-label="팀 메모">
      <Heading>팀 메모</Heading>
      <textarea
        value={draft}
        maxLength={MAX_NOTE_CHARS}
        onChange={(event) => setDraft(event.target.value)}
        rows={5}
        placeholder="회의에 대해 팀이 남길 말을 적어 주세요. 비워 두고 저장하면 지워집니다."
        className="w-full border border-[var(--color-hairline)] text-[var(--color-ink-body)]"
        style={{
          borderRadius: "var(--radius)",
          padding: "var(--space-card)",
          fontSize: "var(--text-body)",
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

function ItemLine({ item }: { item: ActionItemRead }) {
  const late = isOverdue(item);
  return (
    <Line dot={item.status === "done" ? "idle" : "progress"}>
      <span className="text-[var(--color-ink-strong)]">{item.description}</span>
      <Meta>
        {" · "}
        {item.needs_reassignment
          ? "재배정 필요"
          : (item.assignee_name ?? item.assignee_label ?? "담당 미지정")}
        {item.due_date ? (
          <span
            style={{
              fontFamily: "var(--font-mono)",
              color: late ? "var(--color-signal-critical)" : undefined,
            }}
          >
            {" · "}
            {item.due_date}
          </span>
        ) : null}
        {" · "}
        {COLUMN_LABELS[item.status ?? "needs_confirmation"]}
      </Meta>
    </Line>
  );
}

function Line({
  dot,
  children,
}: {
  dot: "confirmed" | "attention" | "progress" | "idle";
  children: React.ReactNode;
}) {
  return (
    <li
      className="flex gap-2 border-b border-[var(--color-hairline)] py-2 last:border-b-0"
      style={{ fontSize: "var(--text-body)" }}
    >
      <StatusDot variant={dot} className="mt-1.5" />
      <div className="min-w-0 flex-1">{children}</div>
    </li>
  );
}

function Count({
  label,
  value,
  alert,
}: {
  label: string;
  value: string;
  alert?: string;
}) {
  return (
    <div>
      <dt className="text-[var(--color-ink-muted)]">{label}</dt>
      <dd className="text-[var(--color-ink-strong)]">
        {value}
        {alert ? (
          <span className="text-[var(--color-signal-critical)]">
            {" "}
            · {alert}
          </span>
        ) : null}
      </dd>
    </div>
  );
}

function Heading({ children }: { children: string }) {
  return (
    <h2
      className="mb-3 border-b border-[var(--color-hairline)] pb-2 text-[var(--color-ink-strong)]"
      style={{
        fontSize: "var(--text-status)",
        fontWeight: "var(--text-status-weight)",
      }}
    >
      {children}
    </h2>
  );
}

function Meta({ children }: { children: React.ReactNode }) {
  return (
    <span
      className="text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-metaSmall)" }}
    >
      {children}
    </span>
  );
}

function Note({ children }: { children: string }) {
  return (
    <p
      className="mb-2 text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-metaSmall)" }}
    >
      {children}
    </p>
  );
}
