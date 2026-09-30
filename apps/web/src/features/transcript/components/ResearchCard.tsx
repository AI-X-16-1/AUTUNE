"use client";

import { useEffect, useState } from "react";

import { getResearch } from "../api";
import type { ResearchDocument } from "../types";

/**
 * Approved research documents for this meeting, above the transcript.
 *
 * Hidden when there are none -- most meetings will have none, and an empty card
 * would say "the agent looked and found nothing", which it may not have. Shown
 * as preformatted text rather than rendered Markdown: no Markdown dependency in
 * the app yet, and the headings read fine as they are.
 */
export function ResearchCard({ meetingId, teamId }: { meetingId: string; teamId: string }) {
  const [docs, setDocs] = useState<ResearchDocument[]>([]);

  useEffect(() => {
    let current = true;
    getResearch(teamId, meetingId)
      .then((list) => {
        if (current) setDocs(list.filter((d) => d.status === "approved"));
      })
      .catch(() => {
        // The card is an extra; a failed read leaves the transcript as it was.
      });
    return () => {
      current = false;
    };
  }, [meetingId, teamId]);

  if (docs.length === 0) return null;
  return (
    <section
      className="mb-4 rounded-[var(--radius)] border border-[var(--color-hairline)] p-4"
      style={{ background: "var(--color-surface-panel)" }}
    >
      <h2
        className="text-[var(--color-ink-strong)]"
        style={{
          fontSize: "var(--text-rowTitle)",
          fontWeight: "var(--text-rowTitle-weight)",
        }}
      >
        리서치
      </h2>
      {docs.map((doc) => (
        <pre
          key={doc.id}
          className="mt-2 whitespace-pre-wrap font-sans text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-meta)" }}
        >
          {doc.body}
        </pre>
      ))}
    </section>
  );
}
