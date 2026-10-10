"use client";

import type { Route } from "next";
import Link from "next/link";
import { useEffect, useState } from "react";

import { ActionBoard } from "./ActionBoard";
import { ActionDetailDrawer } from "./ActionDetailDrawer";
import { CalendarConnect } from "./CalendarConnect";
import { CarriedOverActions } from "./CarriedOverActions";
import { JiraConnect } from "./JiraConnect";
import { MyConfirmations } from "./MyConfirmations";
import { SlackConnect } from "./SlackConnect";
import { NotionConnect } from "./NotionConnect";
import { DecisionReview } from "./DecisionReview";
import { ProjectFilter } from "./ProjectFilter";
import { notExtracted, ReExtract, type NotExtracted } from "./ReExtract";
import { listProjects } from "../api";
import { ALL_PROJECTS, inProject, type ProjectChoice } from "../projectFilter";
import type { Project } from "../types";
import { useActionItems } from "../hooks/useActionItems";
import { useTitleReads } from "../hooks/useTitleReads";
import { awaitsTitle } from "../titleReads";
import { bulkActionItems } from "../api";

/**
 * One meeting's review: its decisions to confirm (S15, #246) above the action
 * board (S17), and the drawer for the item a person opens.
 *
 * The screen lives in the feature rather than in the route file: `apps/` is
 * assembly, and a page that knew how the board and the drawer fit together would
 * be module B's screen kept in the team's shared tree. The route mounts this and
 * passes a meeting id — the same split `features/gap` and `features/transcript`
 * use.
 *
 * The board stays mounted while a refresh runs: it once unmounted whenever an
 * empty meeting was re-read, and took the half-typed add form with it. Only a
 * meeting that has never settled reads as loading. Raised in review of #292.
 *
 * **Loading, failed and empty are three different sentences.** A meeting the
 * pipeline has not reached yet, one whose read failed, and one that genuinely
 * produced no items all render an empty board otherwise, and only the last is
 * true. Items stay on screen when a later reload fails, for the same reason
 * `GapReportScreen` keeps the last good answer.
 *
 * The drawer takes its item from the list rather than holding a copy, so an edit
 * made through it shows on the board and in the drawer at once, and a deleted
 * item closes it.
 *
 * **Column until there is room for two.** The board is `flex-1 min-w-0`, so its
 * flex-shrink weight is zero: beside it on a narrow screen the drawer takes the
 * whole width and the board renders at zero. Raised in review of #292.
 */
export function ActionItemsScreen({ meetingId }: { meetingId: string }) {
  const { items, settled, error, add, edit, close, remove, reload, readTitles } = useActionItems({
    meeting_id: meetingId,
  });
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined);
  // The project filter (2026-10-04): the meeting's team's projects.
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<ProjectChoice>(ALL_PROJECTS);
  useEffect(() => {
    let alive = true;
    listProjects({ meetingId })
      .then((list) => alive && setProjects(list))
      .catch(() => alive && setProjects([]));
    return () => {
      alive = false;
    };
  }, [meetingId]);
  const selected = items.find((item) => item.id === selectedId);
  // Bumped when a "다시 추출" has run: the decisions below read themselves, so
  // they are mounted again rather than told.
  const [extraction, setExtraction] = useState(0);
  // The run's rows get their titles a little after it ends; the board asks
  // for them, and the decisions below do the same for theirs.
  useTitleReads(extraction, awaitsTitle(items), () => void readTitles());
  // An empty list is "not yet" while the first run is going, and "not
  // extracted" when it never came -- not "nothing" (dev, 2026-10-08).
  const [unrun, setUnrun] = useState<NotExtracted>(null);
  // Back from a connection's consent screen, the folded section opens, so the
  // line saying whether it worked is not hidden behind a click.
  const [returning, setReturning] = useState(false);
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    setReturning(CONNECTION_RESULTS.some((key) => query.has(key)));
    // An item pressed on the 요약 tab (#1183) opens here in its window, once
    // the list holding it has arrived; an id not in it opens nothing.
    const asked = query.get("item");
    if (asked) setSelectedId(asked);
  }, []);

  return (
    // The tab row's gutter (see the review layout, #534): starting at the same
    // place as the row above it and the other tabs, not centred under it.
    <main
      className="flex max-w-[1200px] flex-col gap-6 md:flex-row"
      style={{ padding: "20px var(--space-page) var(--space-page)" }}
    >
      <div className="min-w-0 flex-1">
        {/* What this tab is for, first: the items a person confirms. The
            meeting's id was printed here and meant nothing to anyone. */}
        <header>
          <div>
            <h1
              className="text-[var(--color-ink-strong)]"
              style={{
                fontSize: "var(--text-title)",
                fontWeight: "var(--text-title-weight)",
                letterSpacing: "var(--text-title-tracking)",
              }}
            >
              결정과 할 일 확인
            </h1>
            <p
              className="mt-1 text-[var(--color-ink-muted)]"
              style={{ fontSize: "var(--text-meta)" }}
            >
              AI가 회의에서 찾은 항목입니다. 맞으면 확정하고, 틀린 곳은 고쳐 주세요.
            </p>
          </div>
        </header>

        <div className="mt-4">
          <ReExtract
            meetingId={meetingId}
            onState={(read) => setUnrun(notExtracted(read))}
            onExtracted={() => {
              setExtraction((n) => n + 1);
              void reload();
            }}
          />
        </div>

        <div className="mt-6">
          <MyConfirmations meetingId={meetingId} onAnswered={() => void reload()} />
        </div>

        <div className="mt-6">
          <CarriedOverActions meetingId={meetingId} />
        </div>

        <div className="mt-8">
          <DecisionReview key={extraction} run={extraction} meetingId={meetingId} unrun={unrun} />
        </div>

        <div className="mt-8">
          <h2
            className="mb-4 text-[var(--color-ink-strong)]"
            style={{ fontSize: "var(--text-heading)", fontWeight: "var(--text-heading-weight)" }}
          >
            할 일
          </h2>
          {!settled ? (
            <Note>할 일을 불러오는 중입니다.</Note>
          ) : error !== null && items.length === 0 ? (
            <Note>이 회의의 할 일을 불러오지 못했습니다.</Note>
          ) : (
            <>
              {error !== null ? (
                <Note>최신 목록을 불러오지 못해 이전 목록을 보여주고 있습니다.</Note>
              ) : null}
              {items.length === 0 ? (
                <Note>
                  {unrun === "in_progress"
                    ? "이 회의의 할 일을 추출하고 있습니다. 끝나면 여기에 나타납니다."
                    : unrun === "overdue"
                      ? "이 회의의 할 일은 아직 추출되지 않았습니다. 놓친 항목은 직접 추가할 수 있습니다."
                      : "이 회의에서 추출된 할 일이 없습니다. 놓친 항목은 직접 추가할 수 있습니다."}
                </Note>
              ) : null}
              <ProjectFilter projects={projects} value={project} onChange={setProject} />
              <ActionBoard
                items={inProject(items, project)}
                selectedId={selectedId}
                onSelect={setSelectedId}
                add={{ meetingId, onAdd: add }}
                onMove={(id, status) => edit(id, { status })}
                onBulk={async (ids, action) => {
                  const done = await bulkActionItems(ids, action);
                  await reload();
                  return done;
                }}
              />
            </>
          )}
        </div>

        {/* The connections, folded at the foot: they are set once and then
            left alone, and on top they pushed the items below the fold. What
            each one does stays beside its button, as the review of #838 and
            #872 asked, just one click further in. Every connection is also on
            설정 > 연동 (#496). */}
        <details
          open={returning || undefined}
          className="group mt-10 border-t border-[var(--color-hairline)] pt-4"
        >
          <summary
            className="flex cursor-pointer list-none items-center gap-2 text-[var(--color-ink-body)] hover:text-[var(--color-ink-strong)]"
            style={{ fontSize: "var(--text-status)", fontWeight: "var(--text-status-weight)" }}
          >
            <span aria-hidden className="transition-transform group-open:rotate-90">›</span>
            연동 · 캘린더, Jira, Slack, Notion
          </summary>
          <div className="mt-4 flex flex-col gap-3">
            <CalendarConnect />
            <JiraConnect meetingId={meetingId} />
            <SlackConnect meetingId={meetingId} />
            <NotionConnect meetingId={meetingId} />
            <Link
              href={"/settings/integrations" as Route}
              className="text-[var(--color-accent-default)] hover:text-[var(--color-accent-hover)]"
              style={{ fontSize: "var(--text-metaSmall)" }}
            >
              모든 연동 설정 보기
            </Link>
          </div>
        </details>
      </div>

      {selected !== undefined ? (
        <ActionDetailDrawer
          key={selected.id}
          item={selected}
          onClose={() => setSelectedId(undefined)}
          onStatusChange={async (status) => {
            await edit(selected.id, { status });
          }}
          onCloseUnfinished={async () => {
            await close(selected.id);
          }}
          onAssigneeChange={async (change) => {
            await edit(selected.id, change);
          }}
          onDueChange={async (dueDate) => {
            await edit(selected.id, { due_date: dueDate });
          }}
          onDelete={async () => {
            await remove(selected.id);
            setSelectedId(undefined);
          }}
        />
      ) : null}
    </main>
  );
}

/** The query keys each connect button reads when its consent screen sends the person back. */
const CONNECTION_RESULTS = ["calendar", "jira", "slack", "notion"];

function Note({ children }: { children: string }) {
  return (
    <p
      className="mb-4 text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-metaSmall)" }}
    >
      {children}
    </p>
  );
}
