"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

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

/**
 * 승인 대기 — every L2 proposal the signed-in person may decide, across their
 * teams. Approving runs it at once; the card then shows the result and leaves.
 * What a card shows is built by the server at read time; nothing here is stored.
 */
export function ApprovalsScreen() {
  const [items, setItems] = useState<PendingAction[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Record<string, string>>({});

  useEffect(() => {
    let current = true;
    listPending()
      .then((list) => current && setItems(list))
      .catch(
        (e: unknown) =>
          current &&
          setError(e instanceof Error ? e.message : "불러오지 못했습니다"),
      );
    return () => {
      current = false;
    };
  }, []);

  async function decide(item: PendingAction, reason?: RejectReason) {
    try {
      const after = reason
        ? await rejectPending(item.id, reason)
        : await approvePending(item.id);
      const label =
        after.status === "approved"
          ? "승인했습니다"
          : after.status === "rejected"
            ? "거절했습니다"
            : "실행하지 못했습니다";
      setDone((d) => ({ ...d, [item.id]: label }));
    } catch (e: unknown) {
      setDone((d) => ({
        ...d,
        [item.id]: e instanceof Error ? e.message : "처리하지 못했습니다",
      }));
    }
  }

  return (
    <main className="mx-auto max-w-[800px] p-[var(--space-page)]">
      <h1
        className="text-ink-strong"
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
      {items === null ? (
        <p className="mt-4 text-[var(--color-ink-muted)]">불러오는 중…</p>
      ) : null}
      {items?.length === 0 ? (
        <p className="mt-4 text-[var(--color-ink-muted)]">
          승인할 제안이 없습니다.
        </p>
      ) : null}
      <ul className="mt-4 flex flex-col gap-4">
        {items?.map((item) => (
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
                  {" "}
                  ·{" "}
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
            {done[item.id] ? (
              <p className="mt-3" style={{ fontSize: "var(--text-meta)" }}>
                {done[item.id]}
              </p>
            ) : (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button tone="primary" onClick={() => decide(item)}>
                  승인
                </Button>
                {REASONS.map((r) => (
                  <Button key={r.value} onClick={() => decide(item, r.value)}>
                    거절 · {r.label}
                  </Button>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </main>
  );
}
