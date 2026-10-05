"use client";

import { useState } from "react";

import { Button } from "@/shared/ui";

import { assignSummaryProjects, placeActionItem, placeDecision } from "../api";
import type { MeetingSummary, Project } from "../types";

const UNSORTED = "";

/**
 * The 요약 tab's decisions and items grouped by the team's projects (the user,
 * 2026-10-04), when the team has any: one section per project that has a row,
 * then 미분류. Each row can be moved to another project — a person's choice
 * the rules never undo — and "다시 나누기" runs the rules again after a
 * project was added or renamed.
 */
export function ProjectGroups({
  meetingId,
  summary,
  onChange,
}: {
  meetingId: string;
  summary: MeetingSummary;
  onChange: (next: MeetingSummary) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const projects = summary.projects ?? [];
  const meta = { fontSize: "var(--text-metaSmall)" } as const;

  const groups: { project: Project | null; key: string }[] = [
    ...projects.map((project) => ({ project, key: project.id })),
    { project: null, key: UNSORTED },
  ];
  const decisionsIn = (key: string) =>
    summary.decisions.filter((d) => (d.project_id ?? UNSORTED) === key);
  const itemsIn = (key: string) =>
    summary.action_items.filter((i) => (i.project_id ?? UNSORTED) === key);

  const moveDecision = (id: string, to: string) => {
    const projectId = to || null;
    void placeDecision(id, projectId)
      .then(() =>
        onChange({
          ...summary,
          decisions: summary.decisions.map((d) =>
            d.id === id ? { ...d, project_id: projectId } : d,
          ),
        }),
      )
      .catch(() => setNote("옮기지 못했습니다."));
  };
  const moveItem = (id: string, to: string) => {
    void placeActionItem(id, to || null)
      .then((saved) =>
        onChange({
          ...summary,
          action_items: summary.action_items.map((i) =>
            i.id === id ? saved : i,
          ),
        }),
      )
      .catch(() => setNote("옮기지 못했습니다."));
  };

  return (
    <section aria-label="프로젝트별" className="flex flex-col gap-6">
      <div className="flex items-center gap-3">
        <h2
          className="flex-1 border-b border-[var(--color-hairline)] pb-2 text-[var(--color-ink-strong)]"
          style={{
            fontSize: "var(--text-status)",
            fontWeight: "var(--text-status-weight)",
          }}
        >
          프로젝트별
        </h2>
        <Button
          tone="quiet"
          size="compact"
          loading={busy}
          onClick={() => {
            setBusy(true);
            void assignSummaryProjects(meetingId)
              .then((next) => {
                onChange(next);
                setNote(
                  "회의 내용으로 다시 나눴습니다. 직접 옮긴 항목은 그대로입니다.",
                );
              })
              .catch(() => setNote("다시 나누지 못했습니다."))
              .finally(() => setBusy(false));
          }}
        >
          다시 나누기
        </Button>
      </div>
      {note ? (
        <span
          role="status"
          className="text-[var(--color-ink-muted)]"
          style={meta}
        >
          {note}
        </span>
      ) : null}
      {groups.map(({ project, key }) => {
        const decisions = decisionsIn(key);
        const items = itemsIn(key);
        if (decisions.length === 0 && items.length === 0) return null;
        const label = project?.name ?? "미분류";
        return (
          <div key={key || "unsorted"} aria-label={label}>
            <h3
              className="mb-2 text-[var(--color-ink-strong)]"
              style={{ fontSize: "var(--text-body)", fontWeight: 600 }}
            >
              {label}
            </h3>
            <ul>
              {decisions.map((d) => (
                <Row
                  key={d.id}
                  kind="결정"
                  text={d.statement}
                  value={key}
                  projects={projects}
                  onMove={(to) => moveDecision(d.id, to)}
                />
              ))}
              {items.map((i) => (
                <Row
                  key={i.id}
                  kind="할 일"
                  text={i.description}
                  value={key}
                  projects={projects}
                  onMove={(to) => moveItem(i.id, to)}
                />
              ))}
            </ul>
          </div>
        );
      })}
    </section>
  );
}

function Row({
  kind,
  text,
  value,
  projects,
  onMove,
}: {
  kind: string;
  text: string;
  value: string;
  projects: Project[];
  onMove: (to: string) => void;
}) {
  return (
    <li
      className="flex items-center gap-2 border-b border-[var(--color-hairline)] py-2 last:border-b-0"
      style={{ fontSize: "var(--text-body)" }}
    >
      <span
        className="text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        {kind}
      </span>
      <span className="min-w-0 flex-1 text-[var(--color-ink-strong)]">
        {text}
      </span>
      <select
        aria-label={`${text} 프로젝트`}
        value={value}
        onChange={(event) => onMove(event.target.value)}
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
        <option value={UNSORTED}>미분류</option>
      </select>
    </li>
  );
}
