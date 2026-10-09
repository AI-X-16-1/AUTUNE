"use client";

import { useEffect, useMemo, useState } from "react";

import {
  chooseJiraProject,
  disconnectJira,
  getJiraConnection,
  jiraConnectUrl,
  type JiraConnection,
  type IntegrationScope,
} from "@/shared/api/auth";
import { Button } from "@/shared/ui";

import { backfillJira } from "../api";

/**
 * One button to connect the team's Jira (#82, #428). Connected, a confirmed
 * action item becomes a Jira issue in the chosen project, and its assignee,
 * due date and status follow the board.
 *
 * The connection is the team's but the grant is the connecting person's (#82):
 * it lasts while their Atlassian account does. When it lapses the button says
 * "다시 연결" instead of failing quietly.
 *
 * Nothing renders until the status is known, or for someone who is not on the
 * meeting's team.
 *
 * Takes the meeting the 할 일 tab shows, or the team itself on S28 settings
 * (#496); the server checks membership either way.
 */
/** The chosen project in the team's Jira, or the site when none is chosen yet.
 * `/browse/<key>` is the address Jira keeps for a project of any type. */
function jiraLink(siteUrl: string, projectKey?: string | null): string {
  const site = siteUrl.replace(/\/+$/, "");
  return projectKey ? `${site}/browse/${encodeURIComponent(projectKey)}` : site;
}

export function JiraConnect({
  meetingId,
  teamId,
}: {
  meetingId?: string;
  teamId?: string;
}) {
  const scope = useMemo<IntegrationScope>(
    () => (meetingId !== undefined ? { meetingId } : { teamId: teamId ?? "" }),
    [meetingId, teamId],
  );
  const [state, setState] = useState<JiraConnection | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [backfilled, setBackfilled] = useState<string | null>(null);

  const refresh = () => getJiraConnection(scope).then(setState);

  useEffect(() => {
    let alive = true;
    void getJiraConnection(scope).then((status) => {
      if (!alive) return;
      setState(status);
      const url = new URL(window.location.href);
      const result = url.searchParams.get("jira");
      if (result === "connected") setNote("Jira를 연결했습니다.");
      else if (result === "failed") setNote("Jira를 연결하지 못했습니다. 권한에 모두 동의한 채로 다시 시도해 주세요.");
      if (result !== null) {
        url.searchParams.delete("jira");
        window.history.replaceState(null, "", url.toString());
      }
    });
    return () => {
      alive = false;
    };
  }, [scope]);

  if (state === null) return null;

  const connect = () => {
    const here = window.location.pathname + window.location.search;
    window.location.assign(jiraConnectUrl(scope, here));
  };

  const run = async (work: () => Promise<void>, done: string, failed: string) => {
    setBusy(true);
    try {
      await work();
      setNote(done);
      await refresh();
    } catch {
      setNote(failed);
    } finally {
      setBusy(false);
    }
  };

  const meta = { fontSize: "var(--text-metaSmall)" } as const;

  if (!state.connected) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <Button tone="text" size="compact" onClick={connect}>
          팀 Jira 연결
        </Button>
        {note ? <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>{note}</span> : null}
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      {state.needs_reconnect ? (
        <>
          <span className="text-[var(--color-signal-critical)]" style={meta}>
            Jira 연결이 끊겼습니다
          </span>
          <Button tone="text" size="compact" onClick={connect}>
            다시 연결
          </Button>
        </>
      ) : (
        <>
          <span className="text-[var(--color-ink-muted)]" style={meta}>
            Jira 연결됨 · {state.site_name ?? "사이트"}
            {state.project_key ? ` · ${state.project_key}` : ""}
          </span>
          {state.site_url ? (
            <a
              className="text-[var(--color-accent-default)]"
              style={meta}
              href={jiraLink(state.site_url, state.project_key)}
              target="_blank"
              rel="noopener noreferrer"
            >
              Jira에서 열기
            </a>
          ) : null}
        </>
      )}
      {!state.needs_reconnect && state.project_missing ? (
        <span className="text-[var(--color-signal-critical)]" style={meta}>
          프로젝트 {state.project_missing}이(가) Jira에서 없어졌습니다. 새 프로젝트를 고르면 확정된 항목을 모두 다시 넣습니다.
        </span>
      ) : null}
      {!state.needs_reconnect && !state.project_key && state.projects ? (
        <label className="flex items-center gap-2 text-[var(--color-ink-muted)]" style={meta}>
          이슈를 만들 프로젝트
          <select
            disabled={busy}
            defaultValue=""
            onChange={(event) =>
              void run(
                async () => {
                  await chooseJiraProject(scope, event.target.value);
                  const { synced, failed } = await backfillJira(scope);
                  setBackfilled(
                    failed
                      ? `확정된 항목 ${synced}건을 넣었고 ${failed}건은 실패했습니다.`
                      : `확정된 항목 ${synced}건을 이 프로젝트에 넣었습니다.`,
                  );
                },
                "프로젝트를 정했습니다.",
                "프로젝트를 정하지 못했습니다.",
              )
            }
          >
            <option value="" disabled>
              선택
            </option>
            {state.projects.map((project) => (
              <option key={project.key} value={project.key}>
                {project.key} · {project.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <Button
        tone="quiet"
        size="compact"
        loading={busy}
        onClick={() =>
          void run(
            () => disconnectJira(scope),
            "Jira 연결을 해제했습니다. Atlassian 계정의 연결된 앱에서 Autune도 제거해 주세요.",
            "연결을 해제하지 못했습니다.",
          )
        }
      >
        연결 해제
      </Button>
      {note ? <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>{note}</span> : null}
      {backfilled ? <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>{backfilled}</span> : null}
    </div>
  );
}
