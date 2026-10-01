"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/shared/ui";

import { answerConfirmation, getMyConfirmations } from "../api";
import type { ConfirmationAnswer, MyConfirmation } from "../types";

const ANSWERS: { answer: ConfirmationAnswer; label: string }[] = [
  { answer: "commitment", label: "약속입니다" },
  { answer: "decision", label: "결정입니다" },
  { answer: "not_commitment", label: "아닙니다" },
];

const SAID: Record<ConfirmationAnswer, string> = {
  commitment: "약속이라고 답하셨습니다. 요약이 액션 아이템 초안이 됩니다.",
  decision: "결정이라고 답하셨습니다.",
  not_commitment: "약속이 아니라고 답하셨습니다.",
};

/**
 * The questions the confirmation DM links to (#585): the reader's own ambiguous
 * agreements in this meeting, their line as said, and three answers -- the DM's
 * buttons, answered here because a deployed stack has no receiver for a Slack
 * click yet. Only the speaker's own lines ever come back from the server, and
 * nothing renders when there are none.
 *
 * An answer can be changed; "약속입니다" makes the draft at once and its summary
 * a moment later, so `onAnswered` reloads the board.
 */
export function MyConfirmations({
  meetingId,
  onAnswered,
}: {
  meetingId: string;
  onAnswered: () => void;
}) {
  const [rows, setRows] = useState<MyConfirmation[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    getMyConfirmations(meetingId)
      .then((found) => alive && setRows(found))
      .catch(() => alive && setRows([]));
    return () => {
      alive = false;
    };
  }, [meetingId]);

  const answer = useCallback(
    async (utteranceId: string, given: ConfirmationAnswer) => {
      setBusy(utteranceId);
      setFailed(false);
      try {
        const saved = await answerConfirmation(utteranceId, given);
        setRows((now) =>
          now.map((row) => (row.utterance_id === utteranceId ? saved : row)),
        );
        onAnswered();
      } catch {
        setFailed(true);
      } finally {
        setBusy(null);
      }
    },
    [onAnswered],
  );

  if (rows.length === 0) return null;
  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  return (
    <section aria-label="내게 온 확인 요청" className="flex flex-col gap-3">
      <h2
        className="border-b border-[var(--color-hairline)] pb-2 text-[var(--color-ink-strong)]"
        style={{
          fontSize: "var(--text-status)",
          fontWeight: "var(--text-status-weight)",
        }}
      >
        ● 확인이 필요합니다
      </h2>
      <p className="text-[var(--color-ink-muted)]" style={meta}>
        회의에서 하신 말씀을 약속으로 볼지 판단이 서지 않았습니다. 본인에게만
        보입니다.
      </p>
      {failed ? (
        <p role="status" className="text-[var(--color-ink-muted)]" style={meta}>
          답을 저장하지 못했습니다. 다시 시도해 주세요.
        </p>
      ) : null}
      <ul className="flex flex-col gap-3">
        {rows.map((row) => (
          <li key={row.utterance_id} className="flex flex-col gap-2">
            <blockquote className="border-l-2 border-[var(--color-hairline)] pl-3 text-[var(--color-ink-strong)]">
              {row.text}
            </blockquote>
            {row.answer !== null ? (
              <p className="text-[var(--color-ink-muted)]" style={meta}>
                {SAID[row.answer]}
              </p>
            ) : null}
            <div className="flex gap-2">
              {ANSWERS.map(({ answer: given, label }, index) => (
                <Button
                  key={given}
                  tone={index === 0 ? "primary" : "secondary"}
                  disabled={busy !== null || row.answer === given}
                  onClick={() => void answer(row.utterance_id, given)}
                >
                  {label}
                </Button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
