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
 * edited by any member until it is posted. Nothing is posted from this card: an
 * edit goes back to `/approvals` as a new post proposal, and the approval given
 * for the earlier text lapses (#642 review, #674). A posted report is
 * read-only here. `#report-<meeting id>` opens one report directly, once per
 * link: on load, and again when the hash changes on this page (a report row
 * the assistant links to while the dashboard is open, #1055).
 */
export function MeetingReportsCard({ teamId }: { teamId: string }) {
  const { reports, loading, error, save, correct } = useMeetingReports(teamId);
  const [open, setOpen] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const linkApplied = useRef<string | null>(null);
  const [hash, setHash] = useState("");

  useEffect(() => {
    const read = () => setHash(window.location.hash);
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);

  useEffect(() => {
    // Once per link: a save refreshes `reports`, and re-applying the same link
    // would close the row being edited and jump to the linked one (#642 review).
    if (linkApplied.current === hash) return;
    const target = /^#report-(mtg_[A-Za-z0-9]+)$/.exec(hash)?.[1];
    const index = reports.findIndex((r) => r.meeting_id === target);
    if (!target || index < 0) return;
    linkApplied.current = hash;
    if (index >= FIRST_SHOWN) setShowAll(true);
    setOpen(target);
    // After the row renders, so a report past the first five can be reached too.
    requestAnimationFrame(() =>
      document.getElementById(`report-${target}`)?.scrollIntoView({ block: "start" }),
    );
  }, [reports, hash]);

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
                onCorrect={(body) => correct(report.meeting_id, body)}
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
  onCorrect,
}: {
  report: MeetingReport;
  open: boolean;
  onToggle: () => void;
  onSave: (body: string) => Promise<string | null>;
  onCorrect: (body: string) => Promise<string | null>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(report.body);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; failed: boolean } | null>(null);
  const bodyId = `report-body-${report.meeting_id}`;
  const limit = bodyLimit(report);
  const length = characters(draft);

  const startEditing = () => {
    setDraft(report.body);
    setMessage(null);
    setEditing(true);
  };

  const submit = async () => {
    setBusy(true);
    const failure = await onSave(draft);
    setBusy(false);
    setMessage(
      failure
        ? { text: failure, failed: true }
        : {
            text: "저장했습니다. 승인 대기로 보냈습니다 — 승인되면 팀 채널에 올라갑니다.",
            failed: false,
          },
    );
    if (!failure) setEditing(false);
  };

  const [correcting, setCorrecting] = useState(false);
  const [correction, setCorrection] = useState("");

  const startCorrecting = () => {
    setCorrection(report.correction_body ?? report.body);
    setMessage(null);
    setCorrecting(true);
  };

  const sendCorrection = async () => {
    setBusy(true);
    const failure = await onCorrect(correction);
    setBusy(false);
    if (!failure) setCorrecting(false);
    setMessage(
      failure
        ? { text: failure, failed: true }
        : {
            text: "수정본을 승인 대기로 보냈습니다. 승인되면 원래 게시물 아래 스레드에 올라갑니다.",
            failed: false,
          },
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
                본문 편집 · 게시 전까지 팀원 누구나 고칠 수 있습니다. 저장하면 고친 내용으로 승인
                요청이 새로 올라가고, 승인되면 팀 채널에 게시됩니다.
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
                <div style={{ display: "flex", gap: "var(--space-8)", alignItems: "center" }}>
                  <Button tone="secondary" size="compact" onClick={startEditing}>
                    편집
                  </Button>
                  <span style={metaStyle}>게시는 승인 화면에서 승인자가 합니다.</span>
                </div>
              ) : (
                <PostedActions
                  report={report}
                  correcting={correcting}
                  correction={correction}
                  busy={busy}
                  onStart={startCorrecting}
                  onChange={setCorrection}
                  onSend={sendCorrection}
                  onCancel={() => setCorrecting(false)}
                />
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

/**
 * A posted report is never changed in place: people have read it. A member
 * writes a correction; like an edit it waits for an approver on `/approvals`,
 * then goes out as a reply under the post (or as a new message when that thread
 * is out of reach) (#674). The latest one shows here.
 */
function PostedActions({
  report,
  correcting,
  correction,
  busy,
  onStart,
  onChange,
  onSend,
  onCancel,
}: {
  report: MeetingReport;
  correcting: boolean;
  correction: string;
  busy: boolean;
  onStart: () => void;
  onChange: (text: string) => void;
  onSend: () => void;
  onCancel: () => void;
}) {
  const sending = report.correction_status === "sending";
  return (
    <div style={{ display: "grid", gap: "var(--space-8)" }}>
      {report.correction_body ? (
        <div
          style={{
            borderLeft: "2px solid var(--color-hairline)",
            paddingLeft: "var(--space-12)",
            display: "grid",
            gap: "var(--space-4)",
          }}
        >
          <span style={metaStyle}>
            ✏️ 수정본
            {report.corrected_by_name ? ` · ${report.corrected_by_name}` : ""}
            {report.corrected_at ? ` · ${formatTime(report.corrected_at)}` : ""}
            {report.correction_status ? ` · ${CORRECTION_STATUS[report.correction_status]}` : ""}
          </span>
          <div
            style={{
              whiteSpace: "pre-wrap",
              fontSize: "var(--text-body)",
              lineHeight: "var(--text-body-leading)",
              color: "var(--color-ink-body)",
            }}
          >
            <MaskedText>{report.correction_body}</MaskedText>
          </div>
        </div>
      ) : null}
      {!report.in_slack ? (
        <p style={metaStyle}>이 리포트는 Slack에 올라가지 않아 수정본을 보낼 수 없습니다.</p>
      ) : correcting ? (
        <>
          <label htmlFor={`report-correct-${report.meeting_id}`} style={metaStyle}>
            수정본 작성 · 원래 게시물은 그대로 둡니다. 승인되면 그 아래 스레드에 올라갑니다.
          </label>
          <textarea
            id={`report-correct-${report.meeting_id}`}
            value={correction}
            onChange={(event) => onChange(event.target.value)}
            rows={Math.min(18, Math.max(6, correction.split("\n").length + 1))}
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
            <Button tone="primary" size="compact" onClick={onSend} disabled={busy}>
              {busy ? "보내는 중…" : "승인 요청"}
            </Button>
            <Button tone="text" size="compact" onClick={onCancel} disabled={busy}>
              취소
            </Button>
          </div>
        </>
      ) : (
        <div>
          <Button tone="secondary" size="compact" onClick={onStart} disabled={sending}>
            {sending ? "수정본 보내는 중…" : "수정본 쓰기"}
          </Button>
        </div>
      )}
    </div>
  );
}

const CORRECTION_STATUS: Record<NonNullable<MeetingReport["correction_status"]>, string> = {
  pending: "승인 대기",
  sending: "보내는 중",
  sent: "Slack에 올림",
  failed: "보내지 못함 — 다시 쓸 수 있습니다",
};

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
    if (report.status === "draft") parts.push("승인 대기");
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

/** The server's cap counts the whole document as Slack receives it, in code points. */
const DOCUMENT_MAX = 3000;
/** Room for the footer E adds after an edit, which names the editor. */
const EDITED_FOOTER_ROOM = 60;

/**
 * Code points, as the server counts them: an emoji is one, not two. Slack's
 * three control characters go out escaped, so "&" counts as five ("&amp;") and
 * "<" or ">" as four.
 */
function characters(text: string): number {
  return Array.from(text).reduce(
    (count, char) => count + (char === "&" ? 5 : char === "<" || char === ">" ? 4 : 1),
    0,
  );
}

/** How long the body may be once E adds "📋 <title>" above and its footer below. */
function bodyLimit(report: MeetingReport): number {
  return DOCUMENT_MAX - characters(`📋 ${report.title}`) - 4 - EDITED_FOOTER_ROOM;
}

const metaStyle = { margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" };
