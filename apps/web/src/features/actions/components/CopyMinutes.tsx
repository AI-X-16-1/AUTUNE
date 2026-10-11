"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/shared/ui";

import { minutesText, titleOf } from "../minutes";
import type { MeetingSummary } from "../types";
import { ProjectFilter } from "./ProjectFilter";
import { ALL_PROJECTS, inProject, type ProjectChoice } from "../projectFilter";

/**
 * "회의록 복사" on the summary tab: the meeting's decisions and action items
 * as one page of plain text on the clipboard (`minutes.ts`).
 *
 * Says what it leaves out -- no quotation is in it -- because the person
 * pasting it somewhere should not have to wonder. Where the browser will not
 * give the clipboard, the text is shown to select by hand instead of the
 * button just failing.
 *
 * **"복사했습니다." is about one copy, not a standing state** (review of #752).
 * Left on screen it went on saying so after the meeting's content changed, and
 * nobody could tell whether what they held was the new page. It names the page
 * that was copied: it goes after `COPIED_FOR` and at once when the page on
 * screen is no longer that one.
 *
 * **The project chosen here is the page's, not only the copy's** (2026-10-09).
 * The tab draws the same page under this control, so it passes `project` and
 * `onProject` and shows one project's minutes when one is chosen. Used alone,
 * the control keeps the choice itself.
 */

export const COPIED_FOR = 4000;

/** The summary's rows and title as one project's page, or the whole meeting's. */
export function pageOf(
  summary: MeetingSummary,
  project: ProjectChoice,
): { summary: MeetingSummary; title: string | null } {
  const chosen = (summary.projects ?? []).find((p) => p.id === project);
  const title = titleOf(summary);
  return {
    summary: {
      ...summary,
      decisions: inProject(summary.decisions, project),
      action_items: inProject(summary.action_items, project),
    },
    title: chosen ? [title, chosen.name].filter(Boolean).join(" · ") : title,
  };
}

export function CopyMinutes({
  summary,
  project: given,
  onProject,
}: {
  summary: MeetingSummary;
  project?: ProjectChoice;
  onProject?: (next: ProjectChoice) => void;
}) {
  // The page that is on the clipboard, while the notice about it stands.
  const [copied, setCopied] = useState<string | null>(null);
  const [manual, setManual] = useState(false);
  const fading = useRef<number | undefined>(undefined);
  // One project's minutes, when the team lists projects (2026-10-04).
  const [own, setOwn] = useState<ProjectChoice>(ALL_PROJECTS);
  const project = given ?? own;
  const setProject = onProject ?? setOwn;
  const projects = summary.projects ?? [];
  const page = pageOf(summary, project);
  const text = minutesText(page.summary, page.title);

  useEffect(() => () => window.clearTimeout(fading.current), []);

  const copy = async () => {
    window.clearTimeout(fading.current);
    try {
      await navigator.clipboard.writeText(text);
      setManual(false);
      setCopied(text);
      fading.current = window.setTimeout(() => setCopied(null), COPIED_FOR);
    } catch {
      setCopied(null);
      setManual(true);
    }
  };

  const meta = { fontSize: "var(--text-metaSmall)" } as const;

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <ProjectFilter projects={projects} value={project} onChange={setProject} />
        <Button tone="secondary" size="compact" onClick={() => void copy()}>
          회의록 복사
        </Button>
        <span className="text-[var(--color-ink-muted)]" style={meta}>
          회의록을 그대로 복사합니다. 근거 발화 인용은 넣지 않습니다.
        </span>
        {copied !== null && copied === text ? (
          <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>
            복사했습니다.
          </span>
        ) : null}
      </div>
      {manual ? (
        <>
          <p role="alert" className="text-[var(--color-ink-muted)]" style={meta}>
            복사하지 못했습니다. 아래 글을 직접 선택해 복사해 주세요.
          </p>
          <textarea
            readOnly
            aria-label="회의록"
            value={text}
            rows={Math.min(16, text.split("\n").length + 1)}
            onFocus={(event) => event.target.select()}
            className="w-full rounded-[var(--radius)] bg-[var(--color-surface-panel)] p-2 text-[var(--color-ink-body)]"
            style={{ ...meta, border: "1px solid var(--color-hairline)" }}
          />
        </>
      ) : null}
    </div>
  );
}
