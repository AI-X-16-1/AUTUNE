"use client";

import { useState } from "react";

import { Button } from "@/shared/ui";

import { sendSummaryProjects } from "../api";
import type { ProjectSendReport, SendTarget } from "../types";

const TARGETS: { key: SendTarget; label: string }[] = [
  { key: "notion", label: "Notion" },
  { key: "slack", label: "Slack" },
  { key: "jira", label: "Jira" },
  { key: "calendar", label: "내 Google 캘린더" },
];

const OUTCOME: Record<ProjectSendReport["results"][number]["outcome"], string> =
  {
    created: "보냄",
    updated: "고침",
    retracted: "내림",
    not_connected: "연결 안 됨",
    no_date: "회의 날짜 없음",
    failed: "실패",
    // Not "실패": that reads as "try again", and trying again is refused again.
    // The copy carries the project's decisions and items under a title made of
    // the team's and the project's names, and the server does not know which
    // of them was refused (#1133 review): the sentence points at all of them.
    held: "개인정보로 보이는 값이 있어 보내지 않았습니다. 결정·할 일의 문장이나 팀·프로젝트 이름을 고친 뒤 다시 보내 주세요.",
  };

/**
 * "프로젝트별로 보내기" (the user, 2026-10-04): each project's confirmed
 * decisions and items go to the chosen tools as "팀-프로젝트-날짜". Only what
 * a person confirmed leaves (#246); sending again updates the same copies. The
 * answer says, per project and tool, what went.
 */
export function ProjectSend({ meetingId }: { meetingId: string }) {
  const [chosen, setChosen] = useState<SendTarget[]>([
    "notion",
    "slack",
    "jira",
  ]);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<ProjectSendReport | null>(null);
  const [failed, setFailed] = useState(false);
  const meta = { fontSize: "var(--text-metaSmall)" } as const;

  const send = () => {
    setBusy(true);
    setFailed(false);
    void sendSummaryProjects(meetingId, chosen)
      .then(setReport)
      .catch(() => setFailed(true))
      .finally(() => setBusy(false));
  };

  return (
    <div aria-label="프로젝트별로 보내기" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3" style={meta}>
        {TARGETS.map(({ key, label }) => (
          <label key={key} className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={chosen.includes(key)}
              onChange={(event) =>
                setChosen((now) =>
                  event.target.checked
                    ? [...now, key]
                    : now.filter((t) => t !== key),
                )
              }
            />
            {label}
          </label>
        ))}
        <Button
          tone="primary"
          size="compact"
          loading={busy}
          disabled={chosen.length === 0}
          onClick={send}
        >
          프로젝트별로 보내기
        </Button>
      </div>
      <p className="text-[var(--color-ink-muted)]" style={meta}>
        확정된 결정과 할 일만 &quot;팀-프로젝트-회의 날짜&quot;로 보냅니다. 다시
        보내면 같은 페이지·메시지·이슈·일정을 고칩니다. 캘린더는 보내는 사람
        본인의 캘린더에 회의 날짜 종일 일정으로 들어갑니다.
      </p>
      {failed ? (
        <span
          role="status"
          className="text-[var(--color-ink-muted)]"
          style={meta}
        >
          보내지 못했습니다. 다시 시도해 주세요.
        </span>
      ) : null}
      {report ? (
        <div role="status" style={meta}>
          {report.results.length === 0 ? (
            <p className="text-[var(--color-ink-muted)]">
              보낼 것이 없습니다. 프로젝트에 확정된 결정이나 할 일이 있어야
              합니다.
            </p>
          ) : (
            <ul>
              {report.results.map((r) => (
                <li key={`${r.project_id}-${r.target}`}>
                  {r.project_name} ·{" "}
                  {TARGETS.find((t) => t.key === r.target)?.label}{" "}
                  {OUTCOME[r.outcome]}
                </li>
              ))}
            </ul>
          )}
          {report.unsorted > 0 ? (
            <p className="text-[var(--color-ink-muted)]">
              미분류 {report.unsorted}건은 보내지 않았습니다. 프로젝트를 정하면
              함께 보낼 수 있습니다.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
