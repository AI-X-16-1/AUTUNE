"use client";

/**
 * S01 sign-in card — see docs/design/ui-spec.md (S01) and
 * `AUTUNE Spec 01 진입·홈.dc.html`.
 *
 * Three provider paths (Google / Slack / magic link) and three states for the
 * email path: idle, sent (resend after 60s), error (red input + message below).
 * Google is fully wired; Slack and magic link surface a "곧 제공" message until
 * their backends land in W2.
 */
import { useEffect, useState } from "react";

import { Button } from "@/shared/ui/Button";
import {
  ApiError,
  googleStartUrl,
  PENDING_PROVIDERS,
  requestMagicLink,
} from "@/shared/api/auth";

const RESEND_SECONDS = 60;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type Phase = "idle" | "sending" | "sent";

export function SignInCard({ redirectTo = "/" }: { redirectTo?: string }) {
  const [email, setEmail] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(0);

  useEffect(() => {
    if (secondsLeft <= 0) return;
    const timer = setTimeout(() => setSecondsLeft((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [secondsLeft]);

  function startProvider(provider: "google" | "slack") {
    setError(null);
    if (PENDING_PROVIDERS.has(provider)) {
      setError("Slack 로그인은 곧 제공됩니다. 지금은 Google 또는 이메일을 사용해 주세요.");
      return;
    }
    window.location.assign(googleStartUrl(redirectTo));
  }

  async function sendLink() {
    if (!EMAIL_PATTERN.test(email)) {
      setError("올바른 이메일 주소를 입력해 주세요.");
      return;
    }
    setError(null);
    setPhase("sending");
    try {
      await requestMagicLink(email);
      setPhase("sent");
      setSecondsLeft(RESEND_SECONDS);
    } catch (err) {
      setPhase("idle");
      setError(err instanceof ApiError ? err.message : "이메일을 보내지 못했습니다.");
    }
  }

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (phase === "sent" && secondsLeft > 0) return;
    void sendLink();
  }

  const invalid = error !== null && phase !== "sent";

  return (
    <div
      className="w-full max-w-[380px] rounded-[var(--radius)] border border-hairline bg-panel"
      style={{ padding: "var(--space-card)" }}
    >
      <h2
        className="text-ink-strong"
        style={{ fontSize: "var(--text-title)", fontWeight: "var(--text-title-weight)" }}
      >
        시작하기
      </h2>
      <p className="mt-1 text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
        Free · 월 5회 · 카드 등록 없음
      </p>

      <div className="mt-4 flex flex-col gap-2">
        <Button
          type="button"
          tone="primary"
          className="w-full gap-2"
          onClick={() => startProvider("google")}
        >
          <GoogleMark />
          Google로 계속
        </Button>
        <Button
          type="button"
          tone="secondary"
          className="w-full gap-2"
          onClick={() => startProvider("slack")}
        >
          <SlackMark />
          Slack으로 계속
        </Button>
      </div>

      <div
        className="my-4 flex items-center gap-3 text-ink-muted"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        <span className="h-px flex-1 bg-hairline" />
        또는
        <span className="h-px flex-1 bg-hairline" />
      </div>

      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-2">
        <label
          htmlFor="signin-email"
          className="text-ink-muted"
          style={{ fontSize: "var(--text-label)", fontWeight: "var(--text-label-weight)" }}
        >
          이메일 인증 링크 받기
        </label>
        <input
          id="signin-email"
          type="email"
          inputMode="email"
          autoComplete="email"
          placeholder="you@team.com"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            if (error) setError(null);
          }}
          aria-invalid={invalid || undefined}
          aria-describedby={error ? "signin-error" : undefined}
          className="h-[var(--control-h-default)] rounded-[var(--radius)] bg-panel px-3 text-ink-body outline-none"
          style={{
            fontSize: "var(--text-body)",
            border: invalid ? "var(--border-error)" : "var(--border-input)",
          }}
        />

        <Button
          type="submit"
          tone="secondary"
          className="w-full"
          loading={phase === "sending"}
          disabled={phase === "sent" && secondsLeft > 0}
        >
          {phase === "sent"
            ? secondsLeft > 0
              ? `다시 보내기 ${secondsLeft}s`
              : "다시 보내기"
            : "인증 링크 받기"}
        </Button>

        {error && (
          <p
            id="signin-error"
            role="alert"
            style={{ fontSize: "var(--text-metaSmall)", color: "var(--color-signal-critical)" }}
          >
            {error}
          </p>
        )}
        {phase === "sent" && !error && (
          <p className="text-ink-muted" style={{ fontSize: "var(--text-metaSmall)" }}>
            {email}로 인증 링크를 보냈습니다. 메일함을 확인해 주세요.
          </p>
        )}
      </form>

      <p
        className="mt-4 text-ink-muted"
        style={{ fontSize: "var(--text-metaSmall)", lineHeight: "var(--text-metaSmall-leading)" }}
      >
        계속하면 이용약관과 개인정보 처리방침에 동의하는 것입니다. 녹음 원본은 서버에 보관되지
        않습니다.
      </p>
    </div>
  );
}

function GoogleMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 18 18" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.72A5.4 5.4 0 0 1 3.69 9c0-.6.1-1.18.28-1.72V4.95H.96A9 9 0 0 0 0 9c0 1.45.35 2.82.96 4.05l3.01-2.33z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.59C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"
      />
    </svg>
  );
}

function SlackMark() {
  return (
    <svg width="15" height="15" viewBox="0 0 122 122" aria-hidden="true">
      <path
        fill="#E01E5A"
        d="M25.8 77a12.9 12.9 0 1 1-25.8 0 12.9 12.9 0 0 1 12.9-12.9h12.9V77zm6.5 0a12.9 12.9 0 0 1 25.8 0v32.3a12.9 12.9 0 0 1-25.8 0V77z"
      />
      <path
        fill="#36C5F0"
        d="M45.2 25.8a12.9 12.9 0 1 1 0-25.8 12.9 12.9 0 0 1 12.9 12.9v12.9H45.2zm0 6.5a12.9 12.9 0 0 1 0 25.8H12.9a12.9 12.9 0 0 1 0-25.8h32.3z"
      />
      <path
        fill="#2EB67D"
        d="M96.2 45.2a12.9 12.9 0 1 1 25.8 0 12.9 12.9 0 0 1-12.9 12.9H96.2V45.2zm-6.5 0a12.9 12.9 0 0 1-25.8 0V12.9a12.9 12.9 0 0 1 25.8 0v32.3z"
      />
      <path
        fill="#ECB22E"
        d="M76.8 96.2a12.9 12.9 0 1 1 0 25.8 12.9 12.9 0 0 1-12.9-12.9V96.2h12.9zm0-6.5a12.9 12.9 0 0 1 0-25.8h32.3a12.9 12.9 0 0 1 0 25.8H76.8z"
      />
    </svg>
  );
}
