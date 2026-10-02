"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/shared/api/client";
import { Button } from "@/shared/ui/Button";

import { approvePending, listPending, rejectPending } from "../api";
import type { PendingAction, RejectReason } from "../types";

const SUBAGENT_LABEL: Record<string, string> = {
  research: "리서치",
  workload: "업무 분배",
  followup: "후속 회의",
  report: "리포트",
};

const REASONS: { value: RejectReason; label: string }[] = [
  { value: "wrong_evidence", label: "근거가 틀림" },
  { value: "not_now", label: "지금은 아님" },
  { value: "handled_elsewhere", label: "다른 곳에서 처리함" },
  { value: "other", label: "기타" },
];

const RESULT_LABEL: Record<PendingAction["status"], string> = {
  pending: "처리했습니다",
  approved: "승인했습니다",
  rejected: "거절했습니다",
  failed: "실행하지 못했습니다",
  superseded: "다른 제안으로 대체됨",
};

const NEEDS_CHECK = "결과를 확인하지 못했습니다 — 직접 확인해 주세요";
const GONE = "더 이상 없는 제안입니다";
const TRY_LATER = "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";
const LOAD_FAILED = "불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";

/**
 * Whether the server may have acted even though the request failed: a 5xx or a
 * lost response on 승인 (the claim is committed before the action runs), or a
 * 409 on any decision. The list is then re-read rather than a retry offered.
 */
function outcomeUnknown(e: unknown, approving: boolean): boolean {
  if (e instanceof ApiError) {
    return e.status === 409 || (approving && e.status >= 500);
  }
  return approving;
}

/**
 * Merge a fresh list into the one on screen. A card the server still lists
 * takes the server's version; a card settled here stays until the page is left;
 * any other card the server no longer lists goes.
 */
function merge(
  shown: PendingAction[] | null,
  fresh: PendingAction[],
  settled: Record<string, string>,
): PendingAction[] {
  const byId = new Map(fresh.map((i) => [i.id, i]));
  const kept = (shown ?? []).flatMap((i) => {
    const now = byId.get(i.id);
    if (now) return [now];
    return settled[i.id] ? [i] : [];
  });
  const keptIds = new Set(kept.map((i) => i.id));
  return [...fresh.filter((i) => !keptIds.has(i.id)), ...kept];
}

/**
 * 승인 대기 — every L2 proposal the signed-in person may decide, across their
 * teams. Approving runs it at once; the card then shows the result and leaves.
 * What a card shows is built by the server at read time; nothing here is stored.
 */
export function ApprovalsScreen() {
  const [items, setItems] = useState<PendingAction[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Cards that are settled (decided, or found already decided) and what to say.
  const [done, setDone] = useState<Record<string, string>>({});
  // What `done` was at the last render, for a reload that finishes later.
  const doneRef = useRef(done);
  useEffect(() => {
    doneRef.current = done;
  }, [done]);
  // A failed attempt leaves the buttons in place; the message sits beside them.
  const [failed, setFailed] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [choosing, setChoosing] = useState<Set<string>>(new Set());
  const [clicked, setClicked] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    listPending()
      .then((list) => current && setItems(list))
      // Never the API's own message: it is English and meant for a developer.
      .catch(() => current && setError(LOAD_FAILED));
    return () => {
      current = false;
    };
  }, []);

  /**
   * Re-read the list after a decision whose outcome is unknown. The card then
   * reappears as needing a check, goes (someone settled it), or comes back
   * pending, as the server has it. If even the list cannot be read, the card
   * says so and offers nothing to press twice.
   */
  async function reload(id: string) {
    try {
      const fresh = await listPending();
      const settled = { ...doneRef.current };
      delete settled[id];
      setItems((shown) => merge(shown, fresh, settled));
      setDone((d) => {
        const rest = { ...d };
        delete rest[id];
        return rest;
      });
      toggleChoosing(id, false);
    } catch {
      setDone((d) => ({ ...d, [id]: NEEDS_CHECK }));
    }
  }

  function toggleChoosing(id: string, on: boolean) {
    setChoosing((c) => {
      const next = new Set(c);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  async function decide(
    item: PendingAction,
    key: string,
    reason?: RejectReason,
  ) {
    if (busy.has(item.id)) return;
    setBusy((b) => new Set(b).add(item.id));
    setClicked(key);
    setFailed((f) => {
      const rest = { ...f };
      delete rest[item.id];
      return rest;
    });
    try {
      const after = reason
        ? await rejectPending(item.id, reason)
        : await approvePending(item.id);
      setDone((d) => ({
        ...d,
        [item.id]: RESULT_LABEL[after.status] ?? "처리했습니다",
      }));
    } catch (e: unknown) {
      if (outcomeUnknown(e, !reason)) {
        await reload(item.id);
      } else if (e instanceof ApiError && e.status === 404) {
        setDone((d) => ({ ...d, [item.id]: GONE }));
      } else {
        setFailed((f) => ({ ...f, [item.id]: TRY_LATER }));
      }
    } finally {
      setBusy((b) => {
        const next = new Set(b);
        next.delete(item.id);
        return next;
      });
      setClicked(null);
    }
  }

  return (
    <main className="mx-auto max-w-[800px] p-[var(--space-page)]">
      <h1
        className="text-[var(--color-ink-strong)]"
        style={{
          fontSize: "var(--text-title)",
          fontWeight: "var(--text-title-weight)",
        }}
      >
        승인 대기
      </h1>
      {error ? (
        <p role="alert" style={{ color: "var(--color-signal-critical)" }}>
          {error}
        </p>
      ) : null}
      {items === null && !error ? (
        <p className="mt-4 text-[var(--color-ink-muted)]">불러오는 중…</p>
      ) : null}
      {items?.length === 0 ? (
        <p className="mt-4 text-[var(--color-ink-muted)]">
          승인할 제안이 없습니다. 제안은 그 범위의 승인자에게만 보입니다 —{" "}
          <Link href="/settings/approvers" className="underline">
            설정 › 승인자
          </Link>
        </p>
      ) : null}
      <ul className="mt-4 flex flex-col gap-4">
        {items?.map((item) => {
          const isBusy = busy.has(item.id);
          return (
            <li
              key={item.id}
              className="rounded-[var(--radius)] border border-[var(--color-hairline)] p-4"
              style={{ background: "var(--color-surface-panel)" }}
            >
              <div
                className="text-[var(--color-ink-muted)]"
                style={{ fontSize: "var(--text-metaSmall)" }}
              >
                {SUBAGENT_LABEL[item.subagent] ?? item.subagent}
                {item.meeting_id ? (
                  <>
                    {" · "}
                    <Link
                      href={`/meetings/${item.meeting_id}`}
                      className="text-[var(--color-accent-default)]"
                    >
                      회의 보기
                    </Link>
                  </>
                ) : null}
              </div>
              <h2
                className="mt-1 text-[var(--color-ink-strong)]"
                style={{ fontSize: "var(--text-rowTitle)" }}
              >
                {item.title}
              </h2>
              <pre
                className="mt-2 whitespace-pre-wrap font-sans"
                style={{ fontSize: "var(--text-meta)" }}
              >
                {item.body}
              </pre>
              {done[item.id] || item.needs_check ? (
                <p className="mt-3" style={{ fontSize: "var(--text-meta)" }}>
                  {done[item.id] ?? NEEDS_CHECK}
                </p>
              ) : (
                <>
                  <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
                    {choosing.has(item.id) ? (
                      <>
                        {REASONS.map((r) => (
                          <Button
                            key={r.value}
                            tone="text"
                            size="compact"
                            disabled={isBusy}
                            loading={
                              isBusy && clicked === `${item.id}:${r.value}`
                            }
                            onClick={() =>
                              decide(item, `${item.id}:${r.value}`, r.value)
                            }
                          >
                            {r.label}
                          </Button>
                        ))}
                        <Button
                          tone="text"
                          size="compact"
                          disabled={isBusy}
                          onClick={() => toggleChoosing(item.id, false)}
                        >
                          취소
                        </Button>
                      </>
                    ) : (
                      <Button
                        tone="text"
                        size="compact"
                        disabled={isBusy}
                        onClick={() => toggleChoosing(item.id, true)}
                      >
                        거절
                      </Button>
                    )}
                    <Button
                      tone="secondary"
                      size="compact"
                      disabled={isBusy}
                      loading={isBusy && clicked === `${item.id}:approve`}
                      onClick={() => decide(item, `${item.id}:approve`)}
                    >
                      승인
                    </Button>
                  </div>
                  {failed[item.id] ? (
                    <p
                      role="alert"
                      className="mt-2 text-right"
                      style={{
                        fontSize: "var(--text-meta)",
                        color: "var(--color-signal-critical)",
                      }}
                    >
                      {failed[item.id]}
                    </p>
                  ) : null}
                </>
              )}
            </li>
          );
        })}
      </ul>
    </main>
  );
}
