"use client";

import { useRouter } from "next/navigation";
import { useState, type KeyboardEvent } from "react";

import { Button, ChipToggle } from "@/shared/ui";

import { createTeam } from "../api";

/**
 * S02 — make a workspace: its name, my job role, and who else is on it.
 *
 * Somebody who signs in with Google for the first time belongs to no team, and
 * without a team they cannot open a meeting (`POST /meetings` takes a
 * `team_id`). The home screen sends them here; this is the step that makes the
 * rest of the product reachable.
 *
 * **Invitations are memberships made now, not emails.** There is no mail
 * sender. An invited address is put on the team straight away and its owner
 * finds themselves on it the first time they sign in with Google under that
 * address (`service.create_team`). The footer says exactly that, so nobody
 * waits for a message that is not coming.
 *
 * Roles are the S02 chips plus a free entry. The role feeds role-level
 * analytics only (the gap heatmap, role summaries), never anything per person.
 */

const ROLES = ["PM", "Backend", "Frontend", "Design", "Data", "Biz"] as const;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

const INPUT =
  "w-full rounded-[var(--radius)] bg-[var(--color-surface-panel)] px-3 text-[var(--color-ink-strong)] focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)]";
const INPUT_STYLE = {
  height: "var(--control-h-default)",
  fontSize: "var(--text-rowBody)",
  border: "var(--border-input)",
} as const;
const LABEL = {
  fontSize: "var(--text-label)",
  fontWeight: "var(--text-label-weight)",
  color: "var(--color-ink-body)",
} as const;
const HINT = { fontWeight: 400, color: "var(--color-ink-muted)" } as const;

export function WorkspaceScreen() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [role, setRole] = useState<string | null>(null);
  const [customRole, setCustomRole] = useState<string | null>(null);
  const [emails, setEmails] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = name.trim();
  const nameProblem =
    trimmed.length === 0 ? null : trimmed.length < 2 || trimmed.length > 40 ? "2~40자로 입력해 주세요." : null;
  const chosenRole = customRole !== null ? customRole.trim() || null : role;

  const addDraft = (): boolean => {
    const email = draft.trim().toLowerCase().replace(/,$/, "");
    if (!email) return true;
    if (!EMAIL.test(email)) {
      setDraftError("이메일 형식이 아닙니다.");
      return false;
    }
    setEmails((list) => (list.includes(email) ? list : [...list, email]));
    setDraft("");
    setDraftError(null);
    return true;
  };

  const onDraftKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Enter" || event.key === "," || event.key === " ") {
      event.preventDefault();
      addDraft();
    } else if (event.key === "Backspace" && draft === "" && emails.length > 0) {
      setEmails((list) => list.slice(0, -1));
    }
  };

  const submit = async () => {
    // An address still in the box counts; a malformed one stops the submit.
    if (!addDraft()) return;
    const pending = draft.trim().toLowerCase();
    const invite = pending && EMAIL.test(pending) && !emails.includes(pending) ? [...emails, pending] : emails;
    setSubmitting(true);
    setError(null);
    try {
      await createTeam({
        name: trimmed,
        ...(chosenRole ? { role: chosenRole } : {}),
        invite_emails: invite,
      });
      router.replace("/");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "워크스페이스를 만들지 못했습니다.");
      setSubmitting(false);
    }
  };

  const canSubmit = trimmed.length >= 2 && trimmed.length <= 40 && !submitting;

  return (
    <main
      className="grid min-h-screen place-items-center bg-[var(--color-surface-paper)]"
      style={{ padding: "var(--space-24)" }}
    >
      <form
        className="flex w-full max-w-[560px] flex-col rounded-[var(--radius)] bg-[var(--color-surface-panel)]"
        style={{ padding: "var(--space-32)", gap: 20, boxShadow: "var(--shadow-overlay)" }}
        onSubmit={(event) => {
          event.preventDefault();
          if (canSubmit) void submit();
        }}
      >
        {/* Account → workspace → first meeting → done. Signing in was step 1. */}
        <div className="flex gap-1" aria-label="2 / 4 단계">
          {[true, true, false, false].map((done, index) => (
            <span
              key={index}
              className="flex-1"
              style={{
                height: 3,
                background: done ? "var(--color-accent-default)" : "var(--color-surface-sunken)",
              }}
            />
          ))}
        </div>

        <div>
          <h1
            className="text-[var(--color-ink-strong)]"
            style={{
              fontSize: "var(--text-title)",
              fontWeight: "var(--text-title-weight)",
              letterSpacing: "var(--text-title-tracking)",
            }}
          >
            팀 워크스페이스 만들기
          </h1>
          <p className="mt-1.5 text-[var(--color-ink-muted)]" style={{ fontSize: "var(--control-text-default)" }}>
            회의·액션·결정 이력은 워크스페이스 단위로 쌓입니다.
          </p>
        </div>

        <label className="block">
          <span className="mb-1.5 block" style={LABEL}>
            워크스페이스 이름
          </span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="예: 검색 스쿼드"
            maxLength={40}
            autoFocus
            className={INPUT}
            style={nameProblem ? { ...INPUT_STYLE, border: "var(--border-error)" } : INPUT_STYLE}
          />
          {nameProblem && (
            <span className="mt-1 block" style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-critical)" }}>
              {nameProblem}
            </span>
          )}
        </label>

        <div>
          <span className="mb-2 block" style={LABEL}>
            내 직무 <span style={HINT}>— 직무별 요약과 갭 히트맵에 사용</span>
          </span>
          <div className="flex flex-wrap items-center gap-1.5">
            {ROLES.map((option) => (
              <ChipToggle
                key={option}
                selected={customRole === null && role === option}
                onClick={() => {
                  setCustomRole(null);
                  setRole(role === option ? null : option);
                }}
              >
                {option}
              </ChipToggle>
            ))}
            {customRole === null ? (
              <Button tone="text" size="compact" type="button" onClick={() => setCustomRole("")}>
                + 직접 입력
              </Button>
            ) : (
              <input
                value={customRole}
                onChange={(event) => setCustomRole(event.target.value)}
                placeholder="직무"
                maxLength={50}
                autoFocus
                className={INPUT}
                style={{ ...INPUT_STYLE, height: "var(--control-h-compact)", width: 140 }}
              />
            )}
          </div>
        </div>

        <div>
          <span className="mb-1.5 block" style={LABEL}>
            팀원 초대 <span style={HINT}>— 화자 식별과 담당자 매핑에 필요</span>
          </span>
          <div
            className="flex flex-wrap items-center rounded-[var(--radius)] focus-within:ring-[1.5px] focus-within:ring-[var(--color-accent-default)]"
            style={{
              gap: 6,
              padding: "6px 8px",
              minHeight: "var(--control-h-default)",
              border: draftError ? "var(--border-error)" : "var(--border-input)",
            }}
          >
            {emails.map((email) => (
              <span
                key={email}
                className="inline-flex items-center rounded-[var(--radius)] bg-[var(--color-surface-sunken)] text-[var(--color-ink-strong)]"
                style={{ gap: 8, height: 28, padding: "0 10px", fontSize: "var(--text-status)", fontWeight: 500 }}
              >
                {email}
                <button
                  type="button"
                  aria-label={`${email} 빼기`}
                  className="text-[var(--color-ink-muted)] hover:text-[var(--color-ink-strong)]"
                  onClick={() => setEmails((list) => list.filter((item) => item !== email))}
                >
                  ×
                </button>
              </span>
            ))}
            <input
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value);
                setDraftError(null);
              }}
              onKeyDown={onDraftKey}
              onBlur={() => void addDraft()}
              placeholder={emails.length === 0 ? "이메일 입력 후 Enter" : ""}
              aria-label="초대할 이메일"
              className="min-w-[160px] flex-1 bg-transparent text-[var(--color-ink-strong)] outline-none"
              style={{ fontSize: "var(--control-text-default)", height: 28 }}
            />
          </div>
          {draftError && (
            <span className="mt-1 block" style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-critical)" }}>
              {draftError}
            </span>
          )}
        </div>

        {error && (
          <p role="alert" style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-critical)" }}>
            {error}
          </p>
        )}

        <div className="mt-1 flex items-center justify-between gap-4">
          <span className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-meta)" }}>
            초대 메일은 보내지 않아요. 이 주소로 Google 로그인하면 바로 팀에 들어옵니다.
          </span>
          <Button tone="primary" type="submit" disabled={!canSubmit} loading={submitting}>
            만들기
          </Button>
        </div>
      </form>
    </main>
  );
}
