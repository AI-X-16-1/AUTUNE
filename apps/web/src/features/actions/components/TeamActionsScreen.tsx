"use client";

import { useEffect, useMemo, useState } from "react";

import { ChipToggle, Tabs } from "@/shared/ui";

import { ActionBoard } from "./ActionBoard";
import { ActionDetailDrawer } from "./ActionDetailDrawer";
import { JiraOpenIssues } from "./JiraOpenIssues";
import { ProjectFilter } from "./ProjectFilter";
import { ProjectProgressStrip } from "./ProjectProgressStrip";
import { bulkActionItems, listMyProjects, listMyTeams } from "../api";
import { isOverdue, localToday } from "../dates";
import { byProject, byTeam, type BoardView } from "../groups";
import { useActionItems } from "../hooks/useActionItems";
import { ALL_PROJECTS, inProject, type ProjectChoice } from "../projectFilter";
import { projectProgress } from "../projectProgress";
import type { ActionItemRead, Project, TeamName } from "../types";

/**
 * S17 across every meeting — the sidebar's "액션아이템".
 *
 * `ActionItemsScreen` is one meeting's review: its decisions, its connections
 * and its board. This is the board alone, over every item the caller can see.
 * `GET /action-items` with no `meeting_id` already answers that, scoped to the
 * caller's teams by the server (`visible_to`), so nothing new is needed below
 * the screen.
 *
 * The tabs are S17's first three: 전체, 내 담당, 기한 초과. They filter one
 * fetch rather than making three, so their counts are always of the same list.
 * "내 담당" needs the signed-in person's id, which the route passes in — this
 * feature does not read the session. Overdue is a due date before today on an
 * item that is not done; the server's `due_before` would include finished ones.
 *
 * 보기 (the user, 2026-10-06) lays the same items out three ways: 한번에 is one
 * board of everything, 팀별 one board under each team's name, 프로젝트별 one
 * under each project's and then 미분류. A layout and not a filter -- nothing
 * leaves the screen -- so it combines with the tabs and the project filter
 * above it. It opens on 한번에 every time; the choice is not kept. Each item
 * says its team (`team_id`), and `GET /teams/mine` names them.
 *
 * No add form: an item is added to a meeting, and this screen has none. That
 * stays on the meeting's own actions tab.
 *
 * Under the board, the open issues of the Jira projects the caller's teams
 * connected -- viewed on request and never imported (`JiraOpenIssues`).
 */

type Tab = "all" | "mine" | "overdue";

const VIEWS: { id: BoardView; label: string }[] = [
  { id: "all", label: "한번에" },
  { id: "team", label: "팀별" },
  { id: "project", label: "프로젝트별" },
];

export function TeamActionsScreen({ me }: { me: string | null }) {
  const { items, settled, error, edit, remove, reload } = useActionItems({});
  const [tab, setTab] = useState<Tab>("all");
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined);

  const today = localToday();
  const lists = useMemo(() => {
    const mine = me === null ? [] : items.filter((item) => item.assignee_id === me);
    const overdue = items.filter((item) => isOverdue(item, today));
    return { all: items, mine, overdue } satisfies Record<Tab, ActionItemRead[]>;
  }, [items, me, today]);

  // The project filter (2026-10-04), across every team the person is on.
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<ProjectChoice>(ALL_PROJECTS);
  useEffect(() => {
    let alive = true;
    listMyProjects()
      .then((list) => alive && setProjects(list))
      .catch(() => alive && setProjects([]));
    return () => {
      alive = false;
    };
  }, []);

  // The names over each team's board. A read that fails leaves the boards
  // apart and headed as unnamed, rather than merging two teams' items.
  const [view, setView] = useState<BoardView>("all");
  const [teams, setTeams] = useState<TeamName[]>([]);
  useEffect(() => {
    let alive = true;
    listMyTeams()
      .then((list) => alive && setTeams(list))
      .catch(() => alive && setTeams([]));
    return () => {
      alive = false;
    };
  }, []);

  const shown = inProject(lists[tab], project);
  const groups =
    view === "team"
      ? byTeam(shown, teams)
      : view === "project"
        ? byProject(shown, projects, teams)
        : null;
  const progress = useMemo(
    () => projectProgress(items, projects, today),
    [items, projects, today],
  );
  const selected = items.find((item) => item.id === selectedId);

  // One board of `list`: the whole screen's under 한번에, a group's otherwise.
  const board = (list: ActionItemRead[]) => (
    <ActionBoard
      items={list}
      selectedId={selectedId}
      onSelect={setSelectedId}
      showMeeting
      onMove={(id, status) => edit(id, { status })}
      onBulk={async (ids, action) => {
        const done = await bulkActionItems(ids, action);
        await reload();
        return done;
      }}
    />
  );

  return (
    <main
      className="flex flex-col gap-6 md:flex-row"
      style={{ padding: "var(--space-16) var(--space-page) var(--space-page)" }}
    >
      <div className="min-w-0 flex-1">
        <Tabs
          tabs={[
            { id: "all", label: "전체", count: lists.all.length },
            ...(me === null ? [] : [{ id: "mine" as const, label: "내 담당", count: lists.mine.length }]),
            { id: "overdue", label: "기한 초과", count: lists.overdue.length },
          ]}
          active={tab}
          onChange={setTab}
        />

        <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2">
          <div
            role="group"
            aria-label="보기"
            className="flex items-center gap-2 text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-metaSmall)" }}
          >
            보기
            {VIEWS.map(({ id, label }) => (
              <ChipToggle key={id} selected={view === id} onClick={() => setView(id)}>
                {label}
              </ChipToggle>
            ))}
          </div>
          <ProjectFilter projects={projects} value={project} onChange={setProject} />
        </div>

        <ProjectProgressStrip lines={progress} value={project} onChoose={setProject} />

        <div style={{ marginTop: "var(--space-24)" }}>
          {!settled ? (
            <Note>액션 아이템을 불러오는 중입니다.</Note>
          ) : error !== null && items.length === 0 ? (
            <Note>액션 아이템을 불러오지 못했습니다.</Note>
          ) : (
            <>
              {error !== null ? <Note>최신 목록을 불러오지 못해 이전 목록을 보여주고 있습니다.</Note> : null}
              {shown.length === 0 ? <Note>{EMPTY[tab]}</Note> : null}
              {groups === null || shown.length === 0 ? (
                board(shown)
              ) : (
                <div className="flex flex-col" style={{ gap: "var(--space-24)" }}>
                  {groups.map((group) => (
                    <section
                      key={group.key}
                      aria-label={
                        group.note === null ? group.title : `${group.title} · ${group.note}`
                      }
                    >
                      <h2
                        className="mb-3 flex items-baseline gap-2 border-b border-[var(--color-hairline)] pb-2 text-[var(--color-ink-strong)]"
                        style={{
                          fontSize: "var(--text-status)",
                          fontWeight: "var(--text-status-weight)",
                        }}
                      >
                        {group.title}
                        {group.note !== null ? <Meta>{group.note}</Meta> : null}
                        <Meta>{`${group.items.length}건`}</Meta>
                      </h2>
                      {board(group.items)}
                    </section>
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        <JiraOpenIssues />
      </div>

      {selected !== undefined ? (
        <ActionDetailDrawer
          key={selected.id}
          item={selected}
          onClose={() => setSelectedId(undefined)}
          onStatusChange={async (status) => {
            await edit(selected.id, { status });
          }}
          onAssigneeChange={async (change) => {
            await edit(selected.id, change);
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

const EMPTY: Record<Tab, string> = {
  all: "아직 액션 아이템이 없습니다. 회의가 분석되면 이곳에 모입니다.",
  mine: "나에게 배정된 액션 아이템이 없습니다.",
  overdue: "기한이 지난 액션 아이템이 없습니다.",
};

function Meta({ children }: { children: string }) {
  return (
    <span
      className="text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-metaSmall)", fontWeight: "var(--text-body-weight)" }}
    >
      {children}
    </span>
  );
}

function Note({ children }: { children: string }) {
  return (
    <p className="mb-4 text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
      {children}
    </p>
  );
}
