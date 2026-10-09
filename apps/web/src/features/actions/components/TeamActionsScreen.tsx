"use client";

import { useEffect, useMemo, useState } from "react";

import { Button, ChipToggle, Tabs } from "@/shared/ui";

import { ActionBoard } from "./ActionBoard";
import { ActionDetailDrawer } from "./ActionDetailDrawer";
import { JiraOpenIssues } from "./JiraOpenIssues";
import { ProjectFilter } from "./ProjectFilter";
import { ProjectProgressStrip } from "./ProjectProgressStrip";
import { bulkActionItems, listMyProjects, listMyTeams } from "../api";
import { isOverdue, localToday } from "../dates";
import {
  byProject,
  byTeam,
  projectTeams,
  UNNAMED_TEAM,
  type BoardView,
} from "../groups";
import { useActionItems } from "../hooks/useActionItems";
import { ALL_PROJECTS, inProject, UNSORTED, type ProjectChoice } from "../projectFilter";
import { projectProgress } from "../projectProgress";
import type { ActionItemRead, Project, TeamName } from "../types";

/**
 * S17 across every meeting — the sidebar's "할 일".
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
 * **One team's items, when a team is pressed in the sidebar** (the user,
 * 2026-10-08: "액션아이템에서 팀명 누르면 해당 팀의 액션아이템이 뜨는 것으로").
 * The screen still opens on every team. `teamId` narrows it to one: the same
 * fetch, with the other teams' items and projects left out, so the tabs'
 * counts, the project filter, the strip and 보기 are all of that team. A line
 * over the tabs names the team and offers the way back to every team. An item
 * open in the drawer, or a project chosen in the filter, that is not that
 * team's lets go rather than leaving an empty board behind. The route passes
 * the team in: the sidebar's menu is another feature's, and this one does not
 * import it. The Jira issues under the board are that team's too.
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

export function TeamActionsScreen({
  me,
  teamId = null,
  onEveryTeam,
}: {
  me: string | null;
  /** The one team to show; null is every team the person is on. */
  teamId?: string | null;
  /** Back to every team, from the line that names the one team. */
  onEveryTeam?: () => void;
}) {
  const { items: every, settled, error, edit, close, remove, reload } = useActionItems({});
  const items = useMemo(
    () => (teamId === null ? every : every.filter((item) => item.team_id === teamId)),
    [every, teamId],
  );
  const [tab, setTab] = useState<Tab>("all");
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined);

  const today = localToday();
  const lists = useMemo(() => {
    const mine = me === null ? [] : items.filter((item) => item.assignee_id === me);
    const overdue = items.filter((item) => isOverdue(item, today));
    return { all: items, mine, overdue } satisfies Record<Tab, ActionItemRead[]>;
  }, [items, me, today]);

  // The project filter (2026-10-04), across every team the person is on.
  const [everyProject, setProjects] = useState<Project[]>([]);
  const projects = useMemo(
    () =>
      teamId === null
        ? everyProject
        : everyProject.filter((one) => one.team_id === undefined || one.team_id === teamId),
    [everyProject, teamId],
  );
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
  // Two teams can each have a project of one name: the filter and the strip
  // say the team, as the 프로젝트별 groups do.
  const projectTeam = useMemo(
    () => projectTeams(projects, teams),
    [projects, teams],
  );
  const selected = items.find((item) => item.id === selectedId);

  // The team changed under the screen: what was open or chosen and is not on
  // the board any more lets go, so going back to every team does not bring a
  // drawer back, and another team's project does not leave the board empty.
  const [shownTeam, setShownTeam] = useState(teamId);
  if (shownTeam !== teamId) {
    setShownTeam(teamId);
    if (selected === undefined) setSelectedId(undefined);
    if (
      project !== ALL_PROJECTS &&
      project !== UNSORTED &&
      !projects.some((one) => one.id === project)
    )
      setProject(ALL_PROJECTS);
  }
  const teamName =
    teamId === null ? null : (teams.find((team) => team.id === teamId)?.name ?? UNNAMED_TEAM);

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
      style={{ padding: "var(--space-24) var(--space-page) var(--space-page)" }}
    >
      <div className="min-w-0 flex-1">
        {teamName !== null ? (
          <div
            role="status"
            className="mb-3 flex flex-wrap items-center gap-3 text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-metaSmall)" }}
          >
            <span>{`${teamName}의 할 일만 보고 있습니다.`}</span>
            {onEveryTeam !== undefined ? (
              <Button tone="text" size="compact" onClick={onEveryTeam}>
                전체 보기
              </Button>
            ) : null}
          </div>
        ) : null}
        <Tabs
          tabs={[
            { id: "all", label: "전체", count: lists.all.length },
            ...(me === null ? [] : [{ id: "mine" as const, label: "내 담당", count: lists.mine.length }]),
            { id: "overdue", label: "기한 초과", count: lists.overdue.length },
          ]}
          active={tab}
          onChange={setTab}
        />

        {/* The tabs above filter; this row arranges what they let through.
            "묶어 보기" says which of the two it is. */}
        <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2">
          <div
            role="group"
            aria-label="보기"
            className="flex items-center gap-2 text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-label)", fontWeight: "var(--text-label-weight)" }}
          >
            묶어 보기
            {VIEWS.map(({ id, label }) => (
              <ChipToggle key={id} selected={view === id} onClick={() => setView(id)}>
                {label}
              </ChipToggle>
            ))}
          </div>
          <ProjectFilter
            projects={projects}
            teams={projectTeam}
            value={project}
            onChange={setProject}
          />
        </div>

        <ProjectProgressStrip
          lines={progress}
          teams={projectTeam}
          value={project}
          onChoose={setProject}
        />

        <div style={{ marginTop: "var(--space-24)" }}>
          {!settled ? (
            <Note>할 일을 불러오는 중입니다.</Note>
          ) : error !== null && every.length === 0 ? (
            <Note>할 일을 불러오지 못했습니다.</Note>
          ) : (
            <>
              {error !== null ? <Note>최신 목록을 불러오지 못해 이전 목록을 보여주고 있습니다.</Note> : null}
              {shown.length === 0 ? <Empty>{EMPTY[tab]}</Empty> : null}
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
                        className="mb-3 flex items-baseline gap-2 text-[var(--color-ink-strong)]"
                        style={{
                          fontSize: "var(--text-heading)",
                          fontWeight: "var(--text-heading-weight)",
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

        <JiraOpenIssues teamId={teamId} />
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
  all: "아직 할 일이 없습니다. 회의가 분석되면 이곳에 모입니다.",
  mine: "나에게 배정된 할 일이 없습니다.",
  overdue: "기한이 지난 할 일이 없습니다.",
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

/**
 * Nothing on this tab: said in a box of its own, at reading size, so it is
 * not mistaken for a caption above an empty board.
 */
function Empty({ children }: { children: string }) {
  return (
    <p
      className="mb-4 text-center text-[var(--color-ink-body)]"
      style={{
        fontSize: "var(--text-rowBody)",
        padding: "var(--space-24)",
        borderRadius: "var(--radius)",
        border: "1px dashed var(--color-hairline)",
      }}
    >
      {children}
    </p>
  );
}

function Note({ children }: { children: string }) {
  return (
    <p className="mb-4 text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
      {children}
    </p>
  );
}
