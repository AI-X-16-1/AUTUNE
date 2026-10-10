"use client";

import { useEffect, useState } from "react";

import { MaskedText, StatusDot } from "@/shared/ui";

import { listLiveResearch } from "../api";
import type { LiveResearchDocument } from "../types";

/**
 * 회의 중 조사: what the agent looked up while the meeting runs, newest first.
 *
 * A document's first line is the answer and the rest are "- " lines backing
 * it (live model's instructions); sources come from the stored columns, never from the
 * model's text, so a page is listed only when Google Search returned it. Text
 * goes through MaskedText: it was masked before it was stored.
 */
export function LiveResearchPanel({ docs }: { docs: LiveResearchDocument[] }) {
  return (
    <section
      id="live-research"
      aria-label="회의 중 조사"
      style={{ padding: "var(--space-row) var(--space-24)" }}
    >
      <h2
        style={{
          fontSize: "var(--text-rowTitle)",
          fontWeight: "var(--text-rowTitle-weight)",
          color: "var(--color-ink-strong)",
        }}
      >
        회의 중 조사
      </h2>
      {docs.length === 0 ? (
        <p
          style={{
            color: "var(--color-ink-muted)",
            marginTop: "var(--space-8)",
          }}
        >
          확인이 필요한 질문이 나오면 여기에 조사 결과를 띄웁니다. 줄 옆의
          조사를 눌러 직접 요청할 수도 있습니다.
        </p>
      ) : (
        <ol
          style={{
            display: "grid",
            gap: "var(--space-row)",
            marginTop: "var(--space-8)",
          }}
        >
          {docs.map((doc) => (
            <li key={doc.id}>
              <Card doc={doc} />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function Card({ doc }: { doc: LiveResearchDocument }) {
  const [title, ...lines] = (doc.body ?? "")
    .split("\n")
    .filter((l) => l.trim());
  return (
    <article
      style={{
        border: "1px solid var(--color-hairline)",
        borderRadius: "var(--radius-card, 8px)",
        padding: "var(--space-12)",
        background: "var(--color-surface-raised, var(--color-surface-paper))",
      }}
    >
      <div
        className="flex items-center"
        style={{
          gap: "var(--space-8)",
          color: "var(--color-ink-muted)",
          fontSize: "var(--text-meta)",
        }}
      >
        <span>{doc.origin === "auto" ? "자동" : "요청"}</span>
        <span aria-hidden>·</span>
        <MaskedText>{doc.question}</MaskedText>
      </div>
      {doc.status === "running" ? (
        <p
          className="flex items-center"
          style={{ gap: "var(--space-8)", marginTop: "var(--space-8)" }}
        >
          <StatusDot variant="progress" />
          조사 중…
        </p>
      ) : doc.status === "failed" ? (
        <p
          className="flex items-center"
          style={{
            gap: "var(--space-8)",
            marginTop: "var(--space-8)",
            color: "var(--color-signal-attention)",
          }}
        >
          <StatusDot variant="attention" />
          조사하지 못했습니다
        </p>
      ) : (
        <>
          <h3
            style={{
              marginTop: "var(--space-8)",
              fontWeight: "var(--text-rowTitle-weight)",
              color: "var(--color-ink-strong)",
            }}
          >
            <MaskedText>{title ?? doc.question}</MaskedText>
          </h3>
          <ul style={{ marginTop: "var(--space-8)", display: "grid", gap: 4 }}>
            {lines.map((l, i) => (
              <li key={i}>
                <MaskedText>{l.replace(/^-\s*/, "")}</MaskedText>
              </li>
            ))}
          </ul>
          {doc.meeting_sources.length + doc.web_sources.length > 0 ? (
            <ul
              style={{
                marginTop: "var(--space-8)",
                color: "var(--color-ink-muted)",
                fontSize: "var(--text-meta)",
              }}
            >
              {doc.meeting_sources.map((s) => (
                <li key={s.meeting_id}>
                  <MaskedText>{s.title}</MaskedText>
                </li>
              ))}
              {doc.web_sources.map((s) => (
                <li key={s.url}>
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ textDecoration: "underline" }}
                  >
                    {s.title}
                  </a>
                </li>
              ))}
            </ul>
          ) : null}
        </>
      )}
    </article>
  );
}

/** The meeting page's 회의 중 조사: read once, hidden when the meeting has none. */
export function LiveResearchList({ meetingId }: { meetingId: string }) {
  const [docs, setDocs] = useState<LiveResearchDocument[] | null>(null);

  useEffect(() => {
    let current = true;
    setDocs(null);
    listLiveResearch(meetingId)
      .then((list) => {
        if (current) setDocs(list.filter((d) => d.status === "done"));
      })
      .catch(() => {
        if (current) setDocs([]);
      });
    return () => {
      current = false;
    };
  }, [meetingId]);

  // A link to `#live-research` lands here, but this section mounts only after
  // the fetch, so the browser found nothing to scroll to on load.
  const hasDocs = docs !== null && docs.length > 0;
  useEffect(() => {
    if (hasDocs && window.location.hash === "#live-research")
      document
        .getElementById("live-research")
        ?.scrollIntoView({ block: "start" });
  }, [hasDocs]);

  if (docs === null || docs.length === 0)
    return <span data-testid="live-research-empty" hidden />;
  return <LiveResearchPanel docs={docs} />;
}
