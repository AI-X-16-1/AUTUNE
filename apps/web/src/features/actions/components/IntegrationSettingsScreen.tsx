"use client";

import { useEffect, useState } from "react";

import { getSession, type SessionUser } from "@/shared/api/auth";

import { CalendarConnect } from "./CalendarConnect";
import { DueReminderSetting } from "./DueReminderSetting";
import { JiraConnect } from "./JiraConnect";
import { NotionConnect } from "./NotionConnect";
import { SlackConnect } from "./SlackConnect";
import { SlackMeConnect } from "./SlackMeConnect";

type Team = SessionUser["teams"][number];

/**
 * S28, 설정 › 연동 (#496): every connection in one place, with no meeting to
 * name the team. The person's own (Google Calendar, their Slack account) come
 * first; the team's (Slack workspace, Jira, Notion) follow for the team chosen
 * here -- the first by default, a choice when they belong to several. Any
 * member may connect a team's integration: there is no admin role yet (#592).
 *
 * The same components the 액션 tab shows, given the team instead of a meeting;
 * the server checks membership either way.
 */
export function IntegrationSettingsScreen() {
  const [teams, setTeams] = useState<Team[] | null>(null);
  const [teamId, setTeamId] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void getSession().then((user) => {
      if (!alive) return;
      const mine = user?.teams ?? [];
      setTeams(mine);
      setTeamId((current) => current ?? mine[0]?.id ?? null);
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
        <SlackMeConnect />
        <DueReminderSetting />
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
          </>
        )}
        <p className="text-[var(--color-ink-muted)]" style={meta}>
          Notion·Jira로 내보낸 항목은 팀의 기록으로 남습니다. 내 캘린더에 넣은
          기한 일정은 계정을 지우거나 회의 보관 기간이 끝나면 함께 지워집니다.
        </p>
      </section>
    </main>
  );
}
