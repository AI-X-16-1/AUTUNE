"use client";

import { useState } from "react";

import { Button, Row, StatusDot } from "@/shared/ui";

import type { TopicLinkRead } from "../types";

/**
 * One topic link, asserted or pending (S15 context tab, S22 left rail).
 *
 * A dangling link — the linked meeting expired or was deleted by the
 * retention sweep — shows "연결된 회의가 사라짐" instead of a date or a
 * clickable reference. `docs/architecture/privacy.md` forbids reconstructing
 * that meeting's content from its embedding, so this renders the absence and
 * nothing else; `linked_meeting_date` can still be non-null even then (the
 * schema keeps it on purpose), but a bare date with nothing to open reads as
 * a broken link, so it is dropped along with the id.
 */
export function TopicLinkRow({
  link,
  onDecide,
}: {
  link: TopicLinkRead;
  onDecide?: (linkId: number, status: "confirmed" | "rejected") => Promise<unknown>;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dangling = link.linked_meeting_id === null;

  async function decide(status: "confirmed" | "rejected") {
    if (pending || !onDecide) return;
    setPending(true);
    setError(null);
    try {
      await onDecide(link.id, status);
    } catch {
      setError("처리하지 못했습니다. 다시 시도해 주세요.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div>
      <Row
        dot={<StatusDot variant={link.status === "pending" ? "attention" : "confirmed"} hollow={link.status === "pending"} />}
        title={link.topic_label}
        meta={
          dangling
            ? "연결된 회의가 사라짐"
            : [link.linked_meeting_date?.slice(0, 10), `재순위 ${link.rerank_score.toFixed(2)}`]
                .filter(Boolean)
                .join(" · ")
        }
        actions={
          link.status === "pending" && onDecide ? (
            <>
              <Button tone="text" size="compact" disabled={pending} onClick={() => decide("confirmed")}>
                연결 확인
              </Button>
              <Button
                tone="quiet"
                size="compact"
                disabled={pending}
                onClick={() => decide("rejected")}
              >
                아님
              </Button>
            </>
          ) : undefined
        }
      />
      {error && (
        <p
          className="text-[var(--color-signal-critical)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {error}
        </p>
      )}
    </div>
  );
}
