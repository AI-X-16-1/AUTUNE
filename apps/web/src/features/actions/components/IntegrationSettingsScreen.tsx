"use client";

import type { Route } from "next";
import Link from "next/link";
import { useEffect, useState } from "react";

import { getSession, type SessionUser } from "@/shared/api/auth";
import { Button } from "@/shared/ui";

import { CalendarConnect } from "./CalendarConnect";
import { DueReminderSetting } from "./DueReminderSetting";
import { JiraConnect } from "./JiraConnect";
import { NotificationPauseSetting } from "./NotificationPauseSetting";
import { NotionConnect } from "./NotionConnect";
import { ProjectSettings } from "./ProjectSettings";
import { SlackConnect } from "./SlackConnect";
import { SyncLogDrawer } from "./SyncLogDrawer";

type Team = SessionUser["teams"][number];

/**
 * S28, 설정 › 연동 (#496): every connection in one place, with no meeting to
 * name the team. The person's own (Google Calendar) comes first; the team's
 * (Slack workspace, Jira, Notion) follow for the team chosen here -- the one
 * chosen elsewhere in the app when the route passes it (`chosenTeamId`), else
 * the first; a choice when they belong to several, which the route is told
 * (`onChooseTeam`). The feature that keeps the choice is module A's, and a
 * feature does not import another: the route joins the two. The person's own Slack
 * link (DM 받기) is under the team's Slack, shown once that is connected. Any
 * member may connect a team's integration: there is no admin role yet (#592).
 * Under the three, "동기화 기록" opens what the team's action items did on
 * their way out lately (`SyncLogDrawer`).
 *
 * The same components the 할 일 tab shows, given the team instead of a meeting;
 * the server checks membership either way.
 */
export function IntegrationSettingsScreen({
  chosenTeamId = null,
  onChooseTeam,
}: {
  /** The team chosen elsewhere in the app, when the route knows one. */
  chosenTeamId?: string | null;
  /** Told when a team is picked here, so the rest of the app can follow. */
  onChooseTeam?: (teamId: string) => void;
} = {}) {
  const [teams, setTeams] = useState<Team[] | null>(null);
  // The select's own pick, kept only when no route is listening. With a route
  // the pick is handed to it and comes back as `chosenTeamId`: one value, so
  // a team picked here cannot outlive a later choice in the sidebar. Keeping
  // both let the screen show one team while the sidebar marked another
  // (mkkim68, review of #883).
  const [picked, setPicked] = useState<string | null>(null);
  const mine = teams ?? [];
  const known = (id: string | null) => (id !== null && mine.some((t) => t.id === id) ? id : null);
  const own = onChooseTeam === undefined ? known(picked) : null;
  // Derived, so a choice made elsewhere while this screen is open moves it
  // without an effect; the first team when nothing names one of theirs.
  const teamId = own ?? known(chosenTeamId) ?? mine[0]?.id ?? null;
  const setTeamId = onChooseTeam ?? setPicked;
  // The team whose log is open, not a flag: a team chosen while it is open
  // closes it, so the window never shows one team under another's name --
  // and closes it for good, so it does not come back with the first team.
  const [logOf, setLogOf] = useState<string | null>(null);
  if (logOf !== null && logOf !== teamId) setLogOf(null);

  useEffect(() => {
    let alive = true;
    void getSession().then((user) => {
      if (!alive) return;
      setTeams(user?.teams ?? []);
    });
    return () => {
      alive = false;
    };
  }, []);

  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  const heading = {
    fontSize: "var(--text-status)",
    fontWeight: "var(--text-status-weight)",
  } as const;
  return (
    <main
      className="flex max-w-[860px] flex-col gap-8"
      style={{ padding: "20px var(--space-page) var(--space-page)" }}
    >
      <h1
        className="text-[var(--color-ink-strong)]"
        style={{
          fontSize: "var(--text-title)",
          fontWeight: "var(--text-title-weight)",
          letterSpacing: "var(--text-title-tracking)",
        }}
      >
        연동
      </h1>

      <section aria-label="내 연결" className="flex flex-col gap-2">
        <h2 className="text-[var(--color-ink-strong)]" style={heading}>
          내 연결
        </h2>
        <CalendarConnect />
        <DueReminderSetting />
        <NotificationPauseSetting />
      </section>

      <section aria-label="팀 연결" className="flex flex-col gap-2">
        <h2 className="text-[var(--color-ink-strong)]" style={heading}>
          팀 연결
        </h2>
        {teams === null ? (
          <p className="text-[var(--color-ink-muted)]" style={meta}>
            팀을 불러오는 중입니다.
          </p>
        ) : teams.length === 0 || teamId === null ? (
          <p className="text-[var(--color-ink-muted)]" style={meta}>
            속한 팀이 없습니다. 팀에 들어가면 그 팀의 Slack, Jira, Notion을
            연결할 수 있습니다.
          </p>
        ) : (
          <>
            {teams.length > 1 ? (
              <label className="flex items-center gap-2" style={meta}>
                팀
                <select
                  value={teamId}
                  onChange={(event) => setTeamId(event.target.value)}
                >
                  {teams.map((team) => (
                    <option key={team.id} value={team.id}>
                      {team.name}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <SlackConnect key={`slack-${teamId}`} teamId={teamId} />
            <JiraConnect key={`jira-${teamId}`} teamId={teamId} />
            <NotionConnect key={`notion-${teamId}`} teamId={teamId} />
            <div>
              <Button tone="text" size="compact" onClick={() => setLogOf(teamId)}>
                동기화 기록
              </Button>
            </div>
            {logOf === teamId ? (
              <SyncLogDrawer teamId={teamId} onClose={() => setLogOf(null)} />
            ) : null}
            <h3
              className="mt-4 text-[var(--color-ink-strong)]"
              style={heading}
            >
              프로젝트
            </h3>
            <ProjectSettings key={`projects-${teamId}`} teamId={teamId} />
          </>
        )}
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          Notion·Jira로 내보낸 항목은 팀의 기록으로 남습니다. 내 캘린더에 넣은
          기한 일정은 계정을 지우거나 회의 보관 기간이 끝나면 함께 지워집니다.
        </p>
        {/* Where a connection starts sending is where what it sends is said
            (the user, 2026-10-06). The page has one anchor a document, so the
            articles are named in the words. A new tab: this screen is mid-task. */}
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          연결하면 Slack, Notion, Jira, Google로 전달되는 항목과 국외 이전에 관한 내용은{" "}
          <Link
            href={"/legal#privacy" as Route}
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-2"
          >
            개인정보 처리방침
          </Link>{" "}
          제5조(제3자 제공)와 제7조(국외 이전)에서 볼 수 있습니다.
        </p>
      </section>
    </main>
  );
}
