"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/shared/api/client";
import { ChipToggle } from "@/shared/ui";

import { listApprovers, setApproverScopes } from "../api";
import type { Approvers, ApproverScope } from "../types";

const SCOPE_LABEL: Record<ApproverScope, string> = {
  any: "전체",
  report: "리포트",
  research: "리서치",
  followup: "후속 회의",
  workload: "업무 분배",
};

const SECTION_TITLE = {
  fontSize: "var(--text-label)",
  fontWeight: "var(--text-label-weight)",
  color: "var(--color-ink-muted)",
} as const;
const META = {
  fontSize: "var(--text-meta)",
  color: "var(--color-ink-muted)",
} as const;
const ERROR = {
  fontSize: "var(--text-meta)",
  color: "var(--color-signal-critical)",
} as const;

const LOAD_FAILED = "불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
const SAVE_FAILED = "저장하지 못했습니다. 잠시 후 다시 시도해 주세요.";
const NEEDS_ANY =
  "팀에는 '전체' 승인자가 한 명 이상 있어야 합니다. 다른 사람을 먼저 '전체'로 지정해 주세요.";
const NOT_MANAGER = "'전체' 승인자만 승인자를 바꿀 수 있습니다.";

function saveError(e: unknown): string {
  if (e instanceof ApiError && e.status === 409) return NEEDS_ANY;
  if (e instanceof ApiError && e.status === 403) return NOT_MANAGER;
  return SAVE_FAILED;
}

/**
 * 설정 › 승인자 (#592) — who decides the agent's L2 proposals, per scope.
 *
 * A proposal whose scope has no approver reaches nobody, so a new team saw an
 * empty 승인 대기 until someone wrote a row by hand. The rule lives on the
 * server (`main/approvers.py`): while nobody is an approver any member may name
 * one, after that only a `전체` approver may change the list, and the last
 * `전체` approver cannot be removed. This screen draws `can_manage` and the
 * server's refusals; it decides nothing itself.
 */
export function ApproverSettingsScreen({ teamId }: { teamId: string }) {
  const [data, setData] = useState<Approvers | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    listApprovers(teamId)
      .then((fresh) => {
        setData(fresh);
        setError(null);
      })
      .catch(() => setError(LOAD_FAILED));
  }, [teamId]);

  useEffect(() => {
    setData(null);
    load();
  }, [load]);

  const toggle = async (
    userId: string,
    held: ApproverScope[],
    scope: ApproverScope,
  ) => {
    const next = held.includes(scope)
      ? held.filter((s) => s !== scope)
      : [...held, scope];
    setBusy(userId);
    setError(null);
    try {
      await setApproverScopes(teamId, userId, next);
    } catch (e) {
      setError(saveError(e));
    } finally {
      setBusy(null);
      // The first assignment can flip `can_manage`, so read the list again.
      load();
    }
  };

  if (data === null) {
    return error ? (
      <p role="alert" style={ERROR}>
        {error}
      </p>
    ) : (
      <p style={META}>불러오는 중…</p>
    );
  }

  const nobody = data.members.every((m) => m.scopes.length === 0);

  return (
    <section aria-labelledby="approvers-title">
      <h1
        className="text-[var(--color-ink-strong)]"
        style={{
          fontSize: "var(--text-title)",
          fontWeight: "var(--text-title-weight)",
          letterSpacing: "var(--text-title-tracking)",
        }}
      >
        승인자
      </h1>
      <p className="mt-1" style={META}>
        에이전트가 제안한 일을 실행하기 전에 누가 승인할지 범위별로 정합니다
      </p>
      <h2 id="approvers-title" className="mt-6" style={SECTION_TITLE}>
        범위별 승인자
      </h2>
      <p className="mt-1" style={META}>
        {nobody
          ? "아직 승인자가 없어 에이전트의 제안이 누구에게도 보이지 않습니다. 먼저 한 명을 '전체'로 지정해 주세요."
          : data.can_manage
            ? "'전체' 승인자는 모든 제안을 결정하고 승인자 목록도 바꿀 수 있습니다."
            : NOT_MANAGER}
      </p>
      {error && (
        <p role="alert" className="mt-2" style={ERROR}>
          {error}
        </p>
      )}
      <ul className="mt-3">
        {data.members.map((member) => (
          <li
            key={member.user_id}
            className="flex flex-wrap items-center gap-3 border-b border-[var(--color-hairline)]"
            style={{ paddingBlock: "var(--space-row)" }}
          >
            <span
              className="min-w-[120px] flex-1 truncate text-[var(--color-ink-strong)]"
              style={{
                fontSize: "var(--text-rowTitle)",
                fontWeight: "var(--text-rowTitle-weight)",
              }}
            >
              {member.name}
            </span>
            <div
              className="flex flex-wrap gap-1.5"
              aria-busy={busy === member.user_id}
            >
              {data.can_manage ? (
                data.scopes.map((scope) => (
                  <ChipToggle
                    key={scope}
                    selected={member.scopes.includes(scope)}
                    onClick={
                      busy
                        ? undefined
                        : () =>
                            void toggle(member.user_id, member.scopes, scope)
                    }
                  >
                    {SCOPE_LABEL[scope]}
                  </ChipToggle>
                ))
              ) : member.scopes.length === 0 ? (
                <span style={META}>—</span>
              ) : (
                member.scopes.map((scope) => (
                  <span key={scope} style={META}>
                    {SCOPE_LABEL[scope]}
                  </span>
                ))
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
