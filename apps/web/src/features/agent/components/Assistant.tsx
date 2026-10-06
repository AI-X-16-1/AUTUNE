"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { ApiError } from "@/shared/api/client";
import { Button, MaskedText, StatusDot } from "@/shared/ui";

import { getMeetingLabel, listPending, sendChat } from "../api";
import { contextFor } from "../assistantContext";
import type { ChatFinding, ChatReply } from "../types";
import { ChatProposal } from "./ChatProposal";

/**
 * S34 — the assistant: a launcher at the bottom right and a 400×600 chat panel
 * (docs/design/agent-assistant.md, variant 1a).
 *
 * Built against what the agent layer does today, which is narrower than the
 * spec (section 9 there):
 * - A turn is one request and one response; there is no streaming, so the
 *   three dots stand for "waiting" and there is no 중지.
 * - The conversation lives in this component only. Nothing is stored, here or
 *   on the server: an answer can quote any of the team's meetings and must not
 *   outlive one (agent/CLAUDE.md rule 8). A reload starts a new conversation.
 * - An action the subagent proposed is not run from here. L1 already ran on
 *   the server. A decidable L2 proposal gets 승인 / 거절 on the card; the
 *   rest keep the link to 승인 대기, where an approver decides them.
 */

type Turn =
  | { role: "user"; text: string }
  | { role: "assistant"; reply: ChatReply }
  | { role: "system"; text: string };

const UNROUTED =
  "아직 이 질문에는 답할 수 없습니다. 지금은 업무 분배, 회의 리포트, 후속 회의, 회의 브리핑, 리서치를 물어볼 수 있습니다.";
const OFF = "에이전트가 꺼져 있어 답할 수 없습니다.";
const FAILED = "답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.";
const NOT_FOUND = "이 회의를 찾을 수 없습니다.";
const BUSY =
  "지금 AI 사용량이 많아 답하지 못했습니다. 1분쯤 뒤에 다시 물어봐 주세요.";
const PRIVATE =
  "연락처나 계좌번호 같은 개인정보가 들어간 질문은 보낼 수 없습니다. 그 값을 빼고 다시 물어봐 주세요.";

const META = {
  fontSize: "var(--text-metaSmall)",
  color: "var(--color-ink-muted)",
} as const;

function failure(e: unknown, onMeeting: boolean): string {
  // The layer answers 500 with `configuration_error` when it is off or has no
  // model key (autune_core.errors.ConfigurationError).
  if (e instanceof ApiError && e.code === "configuration_error") return OFF;
  // The model is out of quota or down (#419): a minute later usually works.
  if (e instanceof ApiError && e.code === "agent_busy") return BUSY;
  // The outbound guard refused the message itself: retrying cannot help.
  if (e instanceof ApiError && e.code === "privacy_violation") return PRIVATE;
  if (e instanceof ApiError && e.status === 404 && onMeeting) return NOT_FOUND;
  return FAILED;
}

export function Assistant({
  teamId,
  userName,
  pathname,
}: {
  teamId: string;
  userName: string;
  pathname: string;
}) {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [waiting, setWaiting] = useState(false);
  const launcher = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const context = contextFor(pathname);
  const meetingId = context.meetingId;
  // The meeting's own title for the header, once read; the page's name until then.
  // Proposals waiting for this person: the launcher's dot (spec section 2, 9.7).
  const [queued, setQueued] = useState(0);
  const [titled, setTitled] = useState<{ id: string; title: string } | null>(
    null,
  );

  const close = useCallback(() => {
    setOpen(false);
    launcher.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        if (open) close();
        else setOpen(true);
      } else if (e.key === "Escape" && open) {
        close();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);

  useEffect(() => {
    // Read on every page and after every turn: a turn can queue a proposal,
    // and deciding one on 승인 대기 is a navigation away and back. Only rows
    // still pending count: an approval interrupted mid-run (needs_check) is
    // never re-run, so it is nothing to approve (#759 review).
    let current = true;
    listPending()
      .then((rows) => {
        if (current)
          setQueued(rows.filter((r) => r.status === "pending").length);
      })
      .catch(() => {
        if (current) setQueued(0);
      });
    return () => {
      current = false;
    };
  }, [pathname, turns.length]);

  useEffect(() => {
    if (!open || !meetingId || titled?.id === meetingId) return;
    let current = true;
    getMeetingLabel(meetingId)
      .then(({ title }) => {
        if (current) setTitled({ id: meetingId, title });
      })
      .catch(() => {
        // Keep the page's name: the header is a courtesy, not a check.
      });
    return () => {
      current = false;
    };
  }, [open, meetingId, titled?.id]);

  const label =
    meetingId && titled?.id === meetingId ? titled.title : context.label;

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [turns, waiting]);

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || waiting) return;
    setTurns((t) => [...t, { role: "user", text: message }]);
    setDraft("");
    setWaiting(true);
    try {
      // On a meeting page the meeting names its team (a person may be in two).
      const reply = context.meetingId
        ? await sendChat({ meetingId: context.meetingId }, message)
        : await sendChat({ teamId }, message);
      setTurns((t) => [...t, { role: "assistant", reply }]);
    } catch (e) {
      setTurns((t) => [
        ...t,
        { role: "system", text: failure(e, Boolean(context.meetingId)) },
      ]);
    } finally {
      setWaiting(false);
    }
  };

  const onComposerKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(draft);
    }
  };

  const empty = turns.length === 0;

  return (
    <>
      {open && (
        <div
          role="dialog"
          aria-modal="false"
          aria-label="Autune 비서"
          className="fixed z-40 flex flex-col rounded-[var(--radius)] bg-[var(--color-surface-panel)]"
          style={{
            right: 24,
            bottom: 92,
            width: 400,
            height: "min(600px, calc(100vh - 116px))",
            boxShadow:
              "0 2px 12px rgba(22,25,31,.10), 0 0 0 1px var(--color-hairline)",
          }}
        >
          <header
            className="flex shrink-0 items-center justify-between border-b border-[var(--color-hairline)]"
            style={{ height: 52, padding: "0 8px 0 16px" }}
          >
            <div className="min-w-0">
              <span
                className="text-[var(--color-ink-strong)]"
                style={{ fontSize: 14, fontWeight: 600 }}
              >
                Autune 비서
              </span>
              <span
                className="ml-2 truncate"
                style={{ fontSize: 12, color: "var(--color-ink-muted)" }}
              >
                {label} 보고 있음
              </span>
            </div>
            <button
              type="button"
              aria-label="닫기"
              onClick={close}
              className="flex items-center justify-center rounded-[var(--radius)] text-[var(--color-ink-muted)] hover:text-[var(--color-ink-strong)] focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)]"
              style={{ width: 32, height: 32, fontSize: 18 }}
            >
              ×
            </button>
          </header>

          <div
            className="flex flex-1 flex-col overflow-y-auto"
            style={{ padding: 16, gap: 14 }}
            aria-live="polite"
          >
            {empty && (
              <p
                className="text-[var(--color-ink-strong)]"
                style={{ fontSize: 14, fontWeight: 600 }}
              >
                {userName}님, 무엇을 확인할까요?
              </p>
            )}
            {turns.map((turn, i) => (
              <TurnView key={i} turn={turn} />
            ))}
            {waiting && <Waiting />}
            <div ref={bottom} />
          </div>

          <div
            className="shrink-0 border-t border-[var(--color-hairline)]"
            style={{ padding: "12px 16px" }}
          >
            <div className="flex flex-wrap" style={{ gap: 6, marginBottom: 8 }}>
              {context.suggestions.map((q) => (
                <button
                  key={q}
                  type="button"
                  disabled={waiting}
                  onClick={() => void send(q)}
                  className="rounded-[var(--radius)] bg-[var(--color-surface-sunken)] text-[var(--color-ink-body)] disabled:opacity-50"
                  style={{
                    height: empty ? 32 : 28,
                    padding: "0 10px",
                    fontSize: 12,
                    fontWeight: 500,
                  }}
                >
                  {q}
                </button>
              ))}
            </div>
            <div className="flex items-end" style={{ gap: 8 }}>
              <textarea
                ref={input}
                value={draft}
                rows={1}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onComposerKey}
                placeholder="회의·결정·액션에 대해 물어보세요"
                aria-label="비서에게 물어보기"
                maxLength={1000}
                className="flex-1 resize-none rounded-[var(--radius)] border border-[rgba(22,25,31,.2)] bg-[var(--color-surface-panel)] text-[var(--color-ink-strong)] outline-none focus:border-[var(--color-accent-default)] focus:ring-[0.5px] focus:ring-[var(--color-accent-default)]"
                style={{
                  minHeight: 40,
                  maxHeight: 112,
                  padding: "9px 12px",
                  fontSize: 13.5,
                  lineHeight: 1.6,
                }}
              />
              <Button
                tone="primary"
                disabled={waiting || !draft.trim()}
                onClick={() => void send(draft)}
              >
                보내기
              </Button>
            </div>
          </div>
        </div>
      )}

      <button
        ref={launcher}
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        aria-label="Autune 비서 열기 (⌘J)"
        aria-expanded={open}
        className="fixed z-40 flex items-center bg-[var(--color-ink-strong)] text-white hover:bg-black"
        style={{
          right: 24,
          bottom: 24,
          height: 48,
          padding: "0 18px 0 14px",
          borderRadius: 24,
          gap: 8,
          fontSize: 13.5,
          fontWeight: 600,
          boxShadow: "0 2px 12px rgba(22,25,31,.18)",
        }}
      >
        <span
          aria-hidden
          className="flex items-center justify-center rounded-full bg-[var(--color-accent-default)]"
          style={{ width: 22, height: 22, fontSize: 9, fontWeight: 700 }}
        >
          AT
        </span>
        {queued > 0 && (
          <span
            role="status"
            aria-label="승인을 기다리는 제안이 있습니다"
            className="absolute rounded-full bg-[var(--color-signal-critical)]"
            style={{ width: 6, height: 6, left: 32, top: 12 }}
          />
        )}
        비서
        <span
          aria-hidden
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11.5,
            opacity: 0.6,
          }}
        >
          ⌘J
        </span>
      </button>
    </>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  if (turn.role === "user") {
    return (
      <div
        className="self-end whitespace-pre-wrap rounded-[var(--radius)] text-[var(--color-ink-strong)]"
        style={{
          maxWidth: "82%",
          padding: "10px 12px",
          background: "var(--color-accent-selection)",
          fontSize: 13.5,
          lineHeight: 1.6,
        }}
      >
        {turn.text}
      </div>
    );
  }
  if (turn.role === "system") {
    return (
      <p
        role="status"
        className="text-center"
        style={{ fontSize: 12, color: "var(--color-ink-muted)" }}
      >
        {turn.text}
      </p>
    );
  }
  return <AssistantReply reply={turn.reply} />;
}

function AssistantReply({ reply }: { reply: ChatReply }) {
  const unrouted = reply.outcome === "unrouted";
  const decidable = reply.pending ?? [];
  // Waiting for someone else: the server's count, less the ones drawn here.
  const queued = Math.max(reply.queued - decidable.length, 0);
  const unfinished = reply.unfinished ?? [];
  return (
    <div>
      <p
        className="whitespace-pre-wrap text-[var(--color-ink-body)]"
        style={{ fontSize: 13.5, lineHeight: 1.65 }}
      >
        {unrouted ? UNROUTED : <MaskedText>{reply.answer}</MaskedText>}
      </p>
      {!unrouted && reply.items.length > 0 && <Evidence items={reply.items} />}
      {!unrouted &&
        decidable.map((item) => <ChatProposal key={item.id} item={item} />)}
      {!unrouted &&
        (reply.executed > 0 || queued > 0 || unfinished.length > 0) && (
          <div
            className="mt-3 rounded-[var(--radius)]"
            style={{ padding: 12, background: "var(--color-surface-paper)" }}
          >
            {reply.executed > 0 && (
              <p className="flex items-center gap-2" style={{ fontSize: 12.5 }}>
                <StatusDot variant="confirmed" />
                바로 처리한 것 {reply.executed}건
              </p>
            )}
            {unfinished.length > 0 && (
              <>
                <p
                  className="flex items-center gap-2"
                  style={{ fontSize: 12.5 }}
                >
                  <StatusDot variant="attention" />
                  처리하지 못한 것 {unfinished.length}건
                </p>
                <ul style={{ fontSize: 12, paddingLeft: 14 }}>
                  {unfinished.map((item, i) => (
                    <li
                      key={i}
                      className="text-[var(--color-ink-muted)]"
                      style={{ lineHeight: 1.6 }}
                    >
                      <MaskedText>{`${item.title} — ${item.reason}`}</MaskedText>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {queued > 0 && (
              <p className="flex items-center gap-2" style={{ fontSize: 12.5 }}>
                <StatusDot variant="progress" />
                승인이 필요한 제안 {queued}건을 올렸습니다 ·
                <Link
                  href="/approvals"
                  className="text-[var(--color-accent-default)]"
                  style={{ fontWeight: 600 }}
                >
                  승인 대기 열기
                </Link>
              </p>
            )}
          </div>
        )}
    </div>
  );
}

function Evidence({ items }: { items: ChatFinding[] }) {
  return (
    <ul className="mt-3 border-t border-[var(--color-hairline)]">
      {items.map((item, i) => {
        const body = (
          <>
            <span className="mt-[7px] shrink-0">
              <StatusDot variant="confirmed" />
            </span>
            <span className="min-w-0">
              <span
                className="block text-[var(--color-ink-strong)]"
                style={{ fontSize: 13, fontWeight: 500 }}
              >
                <MaskedText>{item.title}</MaskedText>
              </span>
              {item.body && (
                <span className="block" style={META}>
                  <MaskedText>{item.body}</MaskedText>
                </span>
              )}
            </span>
          </>
        );
        return (
          <li
            key={item.id ?? i}
            className="border-b border-[var(--color-hairline)]"
            style={{ padding: "8px 0" }}
          >
            {item.meeting_id ? (
              <Link
                href={`/meetings/${encodeURIComponent(item.meeting_id)}`}
                className="flex gap-2"
              >
                {body}
              </Link>
            ) : (
              <div className="flex gap-2">{body}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function Waiting() {
  return (
    <div className="flex" style={{ gap: 4 }} aria-label="답을 기다리는 중">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="animate-pulse rounded-full motion-reduce:animate-none bg-[var(--color-ink-muted)]"
          style={{
            width: 6,
            height: 6,
            animationDelay: `${i * 0.4}s`,
            animationDuration: "1.2s",
          }}
        />
      ))}
    </div>
  );
}
