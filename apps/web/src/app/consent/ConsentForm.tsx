"use client";

import { useState } from "react";

import { Button } from "@/shared/ui";

import type { RequiredConsent } from "../legal/consents";
import { legalDocument } from "../legal/content";
import { DocumentBody } from "../legal/LegalDocumentView";

/**
 * The documents a person has to agree to, one row each.
 *
 * **A document has to be opened once before its box can be ticked** (the user,
 * 2026-10-02: "유저가 클릭해서 한번 열려야 동의가 되도록"). Opened means its text
 * was put on the screen by the person's own click, here on this page -- not
 * that it was read, which no page can know. The text is the same data, drawn
 * the same way, as `/legal`.
 *
 * Nothing is sent until every box is ticked, and then all of them go in one
 * request: a person is either through the page or still on it, never half.
 * Each row is ticked on its own; there is no "agree to all" box, because one
 * click that ticks every box is the thing the opening rule exists to prevent.
 *
 * The way out without agreeing is to sign out. It is offered here because the
 * page stands in front of every other screen.
 */
export function ConsentForm({
  required,
  onAgree,
  onLeave,
}: {
  required: readonly RequiredConsent[];
  /** Record the agreement. Rejects when it was not recorded. */
  onAgree: (consents: readonly RequiredConsent[]) => Promise<void>;
  onLeave: () => void;
}) {
  const [shown, setShown] = useState<string | null>(null);
  const [opened, setOpened] = useState<ReadonlySet<string>>(new Set());
  const [agreed, setAgreed] = useState<ReadonlySet<string>>(new Set());
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const complete = required.every((item) => agreed.has(item.document));

  const toggle = (document: string) => {
    setShown((current) => (current === document ? null : document));
    setOpened((current) => new Set(current).add(document));
  };

  const tick = (document: string, on: boolean) =>
    setAgreed((current) => {
      const next = new Set(current);
      if (on) next.add(document);
      else next.delete(document);
      return next;
    });

  const submit = async () => {
    setFailure(null);
    setSending(true);
    try {
      await onAgree(required);
    } catch {
      setFailure("동의를 기록하지 못했습니다. 잠시 후 다시 시도해 주세요.");
      setSending(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <ul className="flex flex-col gap-4">
        {required.map(({ document }) => {
          const doc = legalDocument(document);
          const wasOpened = opened.has(document);
          const panel = `consent-${document}`;
          return (
            <li
              key={document}
              className="flex flex-col gap-3 border-b border-hairline pb-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <label
                  className={`flex items-center gap-2 ${wasOpened ? "text-ink-strong" : "text-ink-muted"}`}
                  style={{ fontSize: "var(--text-body)" }}
                >
                  <input
                    type="checkbox"
                    disabled={!wasOpened || sending}
                    checked={agreed.has(document)}
                    onChange={(event) => tick(document, event.target.checked)}
                  />
                  {doc.title}에 동의합니다 (필수)
                </label>
                <Button
                  tone="text"
                  size="compact"
                  aria-expanded={shown === document}
                  aria-controls={panel}
                  onClick={() => toggle(document)}
                >
                  {shown === document ? "접기" : "내용 보기"}
                </Button>
              </div>
              {wasOpened ? null : (
                <p
                  className="text-ink-muted"
                  style={{ fontSize: "var(--text-metaSmall)" }}
                >
                  내용을 한 번 열어 본 뒤에 동의할 수 있습니다.
                </p>
              )}
              {shown === document ? (
                <div
                  id={panel}
                  role="region"
                  aria-label={doc.title}
                  className="flex max-h-[60vh] flex-col gap-6 overflow-y-auto border border-hairline bg-[var(--color-surface-panel)]"
                  style={{
                    borderRadius: "var(--radius)",
                    padding: "var(--space-card)",
                  }}
                >
                  <DocumentBody doc={doc} />
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>

      {failure !== null ? (
        <p
          role="alert"
          className="text-[var(--color-signal-critical)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {failure}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          tone="primary"
          disabled={!complete}
          loading={sending}
          onClick={() => void submit()}
        >
          동의하고 계속
        </Button>
        <Button tone="quiet" disabled={sending} onClick={onLeave}>
          동의하지 않고 로그아웃
        </Button>
      </div>
    </div>
  );
}
