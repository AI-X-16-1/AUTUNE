"use client";

import { useCallback, useState } from "react";

import { Button } from "@/shared/ui";

import type { RequiredConsent } from "../legal/consents";
import { legalDocument } from "../legal/content";
import { DocumentBody } from "../legal/LegalDocumentView";

/**
 * The documents a person has to agree to, one row each.
 *
 * **A document has to be opened by the person's own click before its box can
 * be ticked** (the user, 2026-10-02: "유저가 클릭해서 한번 열려야 동의가 되도록").
 * Scrolling it to its end is not asked for: the user, 2026-10-15, took back the
 * stricter rule of the same day ("스크롤을 끝까지 이동하면 동의") with "이용약관
 * 다 안 읽어도 동의 가능하게". The person opens it with "내용 보기"; the box
 * unlocks at that moment and can be ticked and unticked freely after that. The
 * row's button only opens and closes the text ("내용 보기" / "접기"); it never
 * agrees, so a click meant to close a document cannot record an agreement.
 * Opening is not proof of reading, which no page can know. The text is the
 * same data, drawn the same way, as `/legal`.
 *
 * Nothing is sent until every box is ticked, and then all of them go in one
 * request: a person is either through the page or still on it, never half.
 * Each row is ticked on its own; there is no "agree to all" box, because one
 * click that ticks every box is the thing the opening rule exists to prevent.
 *
 * The way out without agreeing is to sign out. It is offered here because the
 * page stands in front of every other screen. A sign-out that did not succeed
 * is said and the person stays here: the session is still there, and `/login`
 * would only send them back.
 */
export function ConsentForm({
  required,
  onAgree,
  onLeave,
}: {
  required: readonly RequiredConsent[];
  /** Record the agreement. Rejects when it was not recorded. */
  onAgree: (consents: readonly RequiredConsent[]) => Promise<void>;
  /** Sign out. Rejects when the session was not ended. */
  onLeave: () => Promise<void>;
}) {
  const [shown, setShown] = useState<string | null>(null);
  const [reached, setReached] = useState<ReadonlySet<string>>(new Set());
  const [agreed, setAgreed] = useState<ReadonlySet<string>>(new Set());
  const [sending, setSending] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const complete = required.every((item) => agreed.has(item.document));

  const markReached = useCallback(
    (document: string) =>
      setReached((current) =>
        current.has(document) ? current : new Set(current).add(document),
      ),
    [],
  );

  // Opening the text is what unlocks its box; closing it again does not lock
  // it, and a document that was never opened stays locked.
  const toggle = (document: string) => {
    if (shown !== document) markReached(document);
    setShown((current) => (current === document ? null : document));
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

  const leave = async () => {
    setFailure(null);
    setLeaving(true);
    try {
      await onLeave();
    } catch {
      setFailure("로그아웃하지 못했습니다. 잠시 후 다시 시도해 주세요.");
      setLeaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <ul className="flex flex-col gap-4">
        {required.map(({ document }) => {
          const doc = legalDocument(document);
          const wasReached = reached.has(document);
          const isAgreed = agreed.has(document);
          const isShown = shown === document;
          const panel = `consent-${document}`;
          return (
            <li
              key={document}
              className="flex flex-col gap-3 border-b border-hairline pb-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <label
                  className={`flex items-center gap-2 ${wasReached ? "text-ink-strong" : "text-ink-muted"}`}
                  style={{ fontSize: "var(--text-body)" }}
                >
                  <input
                    type="checkbox"
                    disabled={!wasReached || sending}
                    checked={isAgreed}
                    onChange={(event) => tick(document, event.target.checked)}
                  />
                  {doc.title}에 동의합니다 (필수)
                </label>
                <Button
                  tone="text"
                  size="compact"
                  aria-expanded={isShown}
                  aria-controls={panel}
                  disabled={sending}
                  onClick={() => toggle(document)}
                >
                  {isShown ? "접기" : "내용 보기"}
                </Button>
              </div>
              {wasReached ? null : (
                <p
                  className="text-ink-muted"
                  style={{ fontSize: "var(--text-metaSmall)" }}
                >
                  내용 보기를 눌러 내용을 연 뒤에 동의할 수 있습니다.
                </p>
              )}
              {isShown ? (
                <div
                  id={panel}
                  role="region"
                  aria-label={doc.title}
                  // The text is read by scrolling it, so the panel has to be
                  // scrollable from the keyboard in every browser: Safari does
                  // not focus a scroll area that holds nothing focusable.
                  tabIndex={0}
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
          disabled={!complete || leaving}
          loading={sending}
          onClick={() => void submit()}
        >
          동의하고 계속
        </Button>
        <Button
          tone="quiet"
          disabled={sending}
          loading={leaving}
          onClick={() => void leave()}
        >
          동의하지 않고 로그아웃
        </Button>
      </div>
    </div>
  );
}
