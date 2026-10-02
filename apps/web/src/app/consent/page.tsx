"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  agreeToConsents,
  getConsents,
  getSession,
  logout,
} from "@/shared/api/auth";
import { Band } from "@/shared/ui";

import {
  missingConsents,
  REQUIRED_CONSENTS,
  type RequiredConsent,
} from "../legal/consents";
import { COPY_NOTICE } from "../legal/content";
import { Text } from "../legal/LegalDocumentView";
import { ConsentForm } from "./ConsentForm";

/**
 * Where a signed-in person agrees to the terms, the privacy policy and the two
 * separate consents before the app opens (the user, 2026-10-02).
 *
 * Outside `(app)` and `(focus)`, like `/login`: the session gate in front of
 * those sends a person here, so this page cannot stand behind it. It needs a
 * session of its own accord and sends anybody without one to `/login`.
 *
 * It asks only for what is missing. Somebody who agreed to everything and then
 * meets a changed document is shown that document, not all four again.
 *
 * `?next=` is where the gate found the person. Only a path on this site is
 * followed; anything else goes to the home screen.
 */

/** A path on this site, never another origin and never this page again. */
function nextPath(): string {
  const next = new URLSearchParams(window.location.search).get("next") ?? "/";
  const local = next.startsWith("/") && !next.startsWith("//");
  return local && !next.startsWith("/consent") ? next : "/";
}

export default function ConsentPage() {
  const router = useRouter();
  const [missing, setMissing] = useState<RequiredConsent[] | null>(null);

  useEffect(() => {
    let current = true;
    void (async () => {
      const user = await getSession();
      if (!current) return;
      if (user === null) {
        router.replace("/login");
        return;
      }
      const given = await getConsents();
      if (!current) return;
      // Unreadable is treated as nothing given: this page is where a person
      // agrees, and if the record cannot be written either the form says so.
      const left =
        given === null ? [...REQUIRED_CONSENTS] : missingConsents(given);
      if (left.length === 0) router.replace(nextPath());
      else setMissing(left);
    })();
    return () => {
      current = false;
    };
    // Checked once: nothing on this page changes who is signed in.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex min-h-screen flex-col bg-paper">
      <header
        className="flex items-center border-b border-hairline text-ink-strong"
        style={{
          height: "var(--space-topbar)",
          paddingInline: "var(--space-page)",
          fontSize: "var(--text-heading)",
          fontWeight: "var(--text-heading-weight)",
        }}
      >
        AUTUNE
      </header>

      <main
        className="mx-auto flex w-full max-w-[840px] flex-col gap-8"
        style={{ padding: "var(--space-page)" }}
      >
        <Band>
          초안입니다. 법률 검토를 거치지 않았고, 〔 〕로 표시한 항목은 아직
          확정되지 않았습니다.
        </Band>

        <div className="flex flex-col gap-2">
          <h1
            className="text-ink-strong"
            style={{
              fontSize: "var(--text-heading)",
              fontWeight: "var(--text-heading-weight)",
              lineHeight: "var(--text-heading-leading)",
            }}
          >
            시작하기 전에
          </h1>
          <p
            className="text-ink-body"
            style={{
              fontSize: "var(--text-body)",
              lineHeight: "var(--text-body-leading)",
            }}
          >
            아래 문서를 각각 열어 보고 동의해 주세요. 모두 동의하면 Autune으로
            넘어갑니다.
          </p>
          <p
            className="text-ink-muted"
            style={{ fontSize: "var(--text-meta)" }}
          >
            <Text line={COPY_NOTICE} />
          </p>
        </div>

        {missing === null ? null : (
          <ConsentForm
            required={missing}
            onAgree={async (consents) => {
              await agreeToConsents(consents);
              router.replace(nextPath());
            }}
            onLeave={() => {
              void logout().finally(() => router.replace("/login"));
            }}
          />
        )}
      </main>
    </div>
  );
}
