"use client";

import { useEffect, useState, type ReactNode } from "react";

import { MaskedText } from "@/shared/ui";

import { getResearch } from "../api";
import type { ResearchDocument } from "../types";

/**
 * Approved research documents for this meeting, above the transcript.
 *
 * Hidden when there are none -- most meetings will have none, and an empty card
 * would say "the agent looked and found nothing", which it may not have.
 *
 * The writer's document is a fixed shape -- "## " headings and "- " lines
 * (agent research writer) -- so the card draws those two and nothing more,
 * without a Markdown dependency. Text goes through MaskedText, as transcript
 * text does: what the writer quoted was masked before it was stored.
 */
export function ResearchCard({
  meetingId,
  teamId,
}: {
  meetingId: string;
  teamId: string;
}) {
  const [docs, setDocs] = useState<ResearchDocument[]>([]);

  useEffect(() => {
    let current = true;
    // Never show another meeting's documents while this one loads or fails.
    setDocs([]);
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
        <div
          key={doc.id}
          className="mt-2 text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-meta)" }}
        >
          {blocks(doc.body)}
        </div>
      ))}
    </section>
  );
}

/** "## " lines become headings, runs of "- " lines become lists, the rest paragraphs. */
function blocks(body: string): ReactNode[] {
  const out: ReactNode[] = [];
  let items: string[] = [];
  const flush = () => {
    if (items.length === 0) return;
    out.push(
      <ul key={`ul-${out.length}`} className="mt-1 list-disc pl-5">
        {items.map((item, i) => (
          <li key={i}>
            <MaskedText>{item}</MaskedText>
          </li>
        ))}
      </ul>,
    );
    items = [];
  };
  for (const raw of body.split("\n")) {
    const line = raw.trim();
    if (line.startsWith("- ")) {
      items.push(line.slice(2));
      continue;
    }
    flush();
    if (!line) continue;
    if (line.startsWith("## ")) {
      out.push(
        <h3
          key={`h-${out.length}`}
          className="mt-3 first:mt-0"
          style={{ fontWeight: "var(--text-rowTitle-weight)" }}
        >
          {line.slice(3)}
        </h3>,
      );
    } else {
      out.push(
        <p key={`p-${out.length}`} className="mt-1">
          <MaskedText>{line}</MaskedText>
        </p>,
      );
    }
  }
  flush();
  return out;
}
