"use client";

import { useEffect, useRef, useState } from "react";

import { Button, MaskedText } from "@/shared/ui";

import { useMeetingReports } from "../hooks/useMeetingReports";
import type { MeetingReport } from "../types";
import { DashboardCard } from "./DashboardCard";

/**
 * The team's meeting reports: what each one says and whether it went out (10/2).
 *
 * An approver on `/approvals` reads the model's draft here. A draft can be
 * edited by any member until it is posted; an edit is the person's own text,
 * so the approval for the model's text lapses and the person who last edited
 * it posts it from this card (#642 review). A posted report is read-only here.
 * `#report-<meeting id>` opens one report directly, once per page load.
 */
export function MeetingReportsCard({ teamId }: { teamId: string }) {
  const { reports, loading, error, save, post } = useMeetingReports(teamId);
  const [open, setOpen] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const linkApplied = useRef(false);

  useEffect(() => {
    // Once: a save refreshes `reports`, and re-applying the link would close
    // the row being edited and jump to the linked one (#642 review).
    if (linkApplied.current) return;
    const target = /^#report-(mtg_[A-Za-z0-9]+)$/.exec(window.location.hash)?.[1];
    const index = reports.findIndex((r) => r.meeting_id === target);
    if (!target || index < 0) return;
    linkApplied.current = true;
    if (index >= FIRST_SHOWN) setShowAll(true);
    setOpen(target);
    // After the row renders, so a report past the first five can be reached too.
    requestAnimationFrame(() =>
      document.getElementById(`report-${target}`)?.scrollIntoView({ block: "start" }),
    );
  }, [reports]);

  const shown = showAll ? reports : reports.slice(0, FIRST_SHOWN);
  const hidden = reports.length - shown.length;

  return (
    <DashboardCard title="회의 리포트">
      {loading && reports.length === 0 ? (
        <p style={metaStyle}>불러오는 중…</p>
      ) : error ? (
        <p style={{ ...metaStyle, color: "var(--color-signal-critical)" }}>{error}</p>
      ) : reports.length === 0 ? (
        <p style={metaStyle}>
          아직 만들어진 회의 리포트가 없습니다. 회의 분석이 끝나면 초안이 여기에 생깁니다.
        </p>
      ) : (
        <>
          <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
            {shown.map((report) => (
              <ReportRow
                key={report.meeting_id}
                report={report}
                open={open === report.meeting_id}
                onToggle={() =>
                  setOpen((current) => (current === report.meeting_id ? null : report.meeting_id))
                }
                onSave={(body) => save(report, body)}
                onPost={() => post(report.meeting_id)}
              />
            ))}
          </ul>
          {hidden > 0 ? (
            <div
              style={{ borderTop: "1px solid var(--color-hairline)", paddingTop: "var(--space-8)" }}
            >
              <Button tone="text" size="compact" onClick={() => setShowAll(true)}>
                이전 리포트 {hidden}개 더 보기
              </Button>
            </div>
          ) : null}
        </>
      )}
    </DashboardCard>
  );
}

function ReportRow({
  report,
  open,
  onToggle,
  onSave,
  onPost,
}: {
  report: MeetingReport;
  open: boolean;
  onToggle: () => void;
  onSave: (body: string) => Promise<string | null>;
  onPost: () => Promise<string | null>;
}) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [draft, setDraft] = useState(report.body);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; failed: boolean } | null>(null);
  const bodyId = `report-body-${report.meeting_id}`;
  const limit = bodyLimit(report);
  const length = characters(draft);

  const startEditing = () => {
    setDraft(report.body);
    setMessage(null);
    setConfirming(false);
    setEditing(true);
  };

  const submit = async () => {
    setBusy(true);
    const failure = await onSave(draft);
    setBusy(false);
    setMessage(failure ? { text: failure, failed: true } : null);
    if (!failure) setEditing(false);
  };

  const publish = async () => {
    setBusy(true);
    const failure = await onPost();
    setBusy(false);
    setConfirming(false);
    setMessage(
      failure
        ? { text: failure, failed: true }
        : { text: "게시 요청을 보냈습니다. 잠시 후 '게시됨'으로 바뀝니다.", failed: false },
    );
  };

  return (
    <li
      id={`report-${report.meeting_id}`}
      style={{ borderTop: "1px solid var(--color-hairline)", scrollMarginTop: "var(--space-24)" }}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={bodyId}
        title={open ? "접기" : "펼쳐서 본문 보기"}
        className="w-full text-left hover:bg-[var(--color-accent-selection)]"
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "baseline",
          gap: "var(--space-4) var(--space-12)",
          padding: "var(--space-row) var(--space-4)",
          background: "none",
          border: 0,
          cursor: "pointer",
        }}
      >
        <span
          aria-hidden="true"
          style={{
            display: "inline-block",
            width: "1em",
            color: "var(--color-ink-body)",
            transform: open ? "rotate(90deg)" : "none",
          }}
        >
          ▸
        </span>
        <span style={{ color: "var(--color-ink-strong)", fontSize: "var(--text-body)" }}>
          {report.title}
        </span>
        <StatusChip report={report} />
        <span style={{ ...metaStyle, marginLeft: "auto" }}>{rowMeta(report)}</span>
      </button>

      {open ? (
        <div
          id={bodyId}
          style={{ paddingBottom: "var(--space-16)", display: "grid", gap: "var(--space-8)" }}
        >
          {editing ? (
            <>
              <label htmlFor={`report-edit-${report.meeting_id}`} style={metaStyle}>
                본문 편집 · 게시 전까지 팀원 누구나 고칠 수 있습니다. 고치면 승인 요청은 효력이
                없어지고, 마지막으로 고친 사람이 여기서 직접 게시합니다.
              </label>
              <textarea
                id={`report-edit-${report.meeting_id}`}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                rows={Math.min(18, Math.max(6, draft.split("\n").length + 1))}
                style={{
                  width: "100%",
                  font: "inherit",
                  fontSize: "var(--text-body)",
                  lineHeight: "var(--text-body-leading)",
                  color: "var(--color-ink-body)",
                  background: "var(--color-surface-panel)",
                  border: "1px solid var(--color-hairline)",
                  borderRadius: "var(--radius)",
                  padding: "var(--space-8)",
                }}
              />
              <div style={{ display: "flex", gap: "var(--space-8)", alignItems: "center" }}>
                <Button tone="primary" size="compact" onClick={submit} disabled={busy}>
                  {busy ? "저장 중…" : "저장"}
                </Button>
                <Button
                  tone="text"
                  size="compact"
                  onClick={() => setEditing(false)}
                  disabled={busy}
                >
                  취소
                </Button>
                <span
                  style={{
                    ...metaStyle,
                    color: length > limit ? "var(--color-signal-critical)" : metaStyle.color,
                  }}
                >
                  본문 {length.toLocaleString()} / {limit.toLocaleString()}자
                </span>
              </div>
            </>
          ) : (
            <>
              <div
                style={{
                  maxHeight: "60vh",
                  overflowY: "auto",
                  whiteSpace: "pre-wrap",
                  fontSize: "var(--text-body)",
                  lineHeight: "var(--text-body-leading)",
                  color: "var(--color-ink-body)",
                }}
              >
                <MaskedText>{report.body}</MaskedText>
              </div>
              {report.footer ? <p style={metaStyle}>{report.footer}</p> : null}
              {report.status === "draft" ? (
                confirming ? (
                  <div style={{ display: "flex", gap: "var(--space-8)", alignItems: "center" }}>
                    <span style={metaStyle}>이 내용을 팀 채널에 바로 게시할까요?</span>
                    <Button tone="primary" size="compact" onClick={publish} disabled={busy}>
                      {busy ? "게시 중…" : "게시"}
                    </Button>
                    <Button
                      tone="text"
                      size="compact"
                      onClick={() => setConfirming(false)}
                      disabled={busy}
                    >
                      취소
                    </Button>
                  </div>
                ) : (
                  <div style={{ display: "flex", gap: "var(--space-8)" }}>
                    <Button tone="secondary" size="compact" onClick={startEditing}>
                      편집
                    </Button>
                    {report.can_post ? (
                      <Button tone="primary" size="compact" onClick={() => setConfirming(true)}>
                        게시
                      </Button>
                    ) : null}
                  </div>
                )
              ) : (
                <p style={metaStyle}>게시된 리포트는 여기서 고칠 수 없습니다.</p>
              )}
            </>
          )}
          {message ? (
            <p
              role={message.failed ? "alert" : "status"}
              style={{
                ...metaStyle,
                color: message.failed ? "var(--color-signal-critical)" : metaStyle.color,
              }}
            >
              {message.text}
            </p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function StatusChip({ report }: { report: MeetingReport }) {
  const posted = report.status === "posted";
  return (
    <span
      style={{
        fontSize: "var(--text-meta)",
        padding: "0 var(--space-8)",
        borderRadius: "var(--radius)",
        color: posted ? "var(--color-ink-muted)" : "var(--color-accent-default)",
        background: posted ? "var(--color-surface-sunken)" : "var(--color-accent-selection)",
      }}
    >
      {posted ? "게시됨" : "초안"}
    </span>
  );
}

function rowMeta(report: MeetingReport): string {
  const parts: string[] = [];
  if (report.status === "posted" && report.posted_at) {
    parts.push(`게시 ${formatTime(report.posted_at)}`);
  }
  if (report.edited_by_name && report.edited_at) {
    parts.push(`${report.edited_by_name} 수정 ${formatTime(report.edited_at)}`);
  } else if (report.status === "draft") {
    // `updated_at` moves on every edit, so it is the draft time only until one.
    parts.push(`작성 ${formatTime(report.updated_at)}`);
  }
  return parts.join(" · ");
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Rows shown before "이전 리포트 n개 더 보기"; the server sends at most 20. */
const FIRST_SHOWN = 5;

/** The server's cap counts the whole stored document, in code points. */
const DOCUMENT_MAX = 3000;
/** Room for the footer E writes after an edit, which names the editor. */
const EDITED_FOOTER_ROOM = 60;

/** Code points, as the server counts them: an emoji is one, not two. */
function characters(text: string): number {
  return Array.from(text).length;
}

/** How long the body may be once E adds "📋 <title>" above and its footer below. */
function bodyLimit(report: MeetingReport): number {
  return DOCUMENT_MAX - characters(`📋 ${report.title}`) - 4 - EDITED_FOOTER_ROOM;
}

const metaStyle = { margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" };
