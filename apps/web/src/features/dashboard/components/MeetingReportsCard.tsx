"use client";

import { useEffect, useState } from "react";

import { Button, MaskedText } from "@/shared/ui";

import { useMeetingReports } from "../hooks/useMeetingReports";
import type { MeetingReport } from "../types";
import { DashboardCard } from "./DashboardCard";

/**
 * The team's meeting reports: what each one says and whether it went out (10/2).
 *
 * An approver on `/approvals` reads the text here before a post goes to the
 * team channel. A draft can be edited by any member until it is posted; the
 * editor and time are shown, and the pending approval stays -- it posts the
 * edited text. A posted report is read-only here. `#report-<meeting id>` opens
 * one report directly.
 */
export function MeetingReportsCard({ teamId }: { teamId: string }) {
  const { reports, loading, error, save } = useMeetingReports(teamId);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    const target = /^#report-(mtg_[A-Za-z0-9]+)$/.exec(window.location.hash)?.[1];
    if (!target || !reports.some((r) => r.meeting_id === target)) return;
    setOpen(target);
    document.getElementById(`report-${target}`)?.scrollIntoView({ block: "start" });
  }, [reports]);

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
        <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
          {reports.map((report) => (
            <ReportRow
              key={report.meeting_id}
              report={report}
              open={open === report.meeting_id}
              onToggle={() =>
                setOpen((current) => (current === report.meeting_id ? null : report.meeting_id))
              }
              onSave={(body) => save(report.meeting_id, body)}
            />
          ))}
        </ul>
      )}
    </DashboardCard>
  );
}

function ReportRow({
  report,
  open,
  onToggle,
  onSave,
}: {
  report: MeetingReport;
  open: boolean;
  onToggle: () => void;
  onSave: (body: string) => Promise<string | null>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(report.body);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const bodyId = `report-body-${report.meeting_id}`;

  const startEditing = () => {
    setDraft(report.body);
    setMessage(null);
    setEditing(true);
  };

  const submit = async () => {
    setSaving(true);
    const failure = await onSave(draft);
    setSaving(false);
    setMessage(failure);
    if (!failure) setEditing(false);
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
        className="w-full text-left"
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "baseline",
          gap: "var(--space-4) var(--space-12)",
          padding: "var(--space-row) 0",
          background: "none",
          border: 0,
          cursor: "pointer",
        }}
      >
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
                본문 편집 · 게시 전까지 팀원 누구나 고칠 수 있고, 고친 사람과 시각이 남습니다.
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
                <Button tone="primary" size="compact" onClick={submit} disabled={saving}>
                  {saving ? "저장 중…" : "저장"}
                </Button>
                <Button
                  tone="text"
                  size="compact"
                  onClick={() => setEditing(false)}
                  disabled={saving}
                >
                  취소
                </Button>
                <span style={metaStyle}>
                  {draft.length.toLocaleString()} / 3,000자 이하(머리말 포함)
                </span>
              </div>
            </>
          ) : (
            <>
              <div
                style={{
                  whiteSpace: "pre-wrap",
                  fontSize: "var(--text-body)",
                  lineHeight: "var(--text-body-leading)",
                  color: "var(--color-ink-body)",
                }}
              >
                <MaskedText>{report.body}</MaskedText>
              </div>
              {report.status === "draft" ? (
                <div>
                  <Button tone="secondary" size="compact" onClick={startEditing}>
                    편집
                  </Button>
                </div>
              ) : (
                <p style={metaStyle}>게시된 리포트는 여기서 고칠 수 없습니다.</p>
              )}
            </>
          )}
          {message ? (
            <p role="alert" style={{ ...metaStyle, color: "var(--color-signal-critical)" }}>
              {message}
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

const metaStyle = { margin: 0, fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" };
