"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/shared/api/client";
import { Button } from "@/shared/ui";

import { getExtractionState, requestExtraction, type ExtractionState } from "../api";

/** How often a requested run is looked for, and for how long. */
export const POLL_MS = 5_000;
export const POLL_LIMIT = 60;
/** Reads of a first run that may fail in a row before the screen stops asking. */
export const MISS_LIMIT = 12;

/** Why a board with no rows is not yet "this meeting produced nothing". */
export type NotExtracted = "in_progress" | "overdue" | null;
export const notExtracted = (state: ExtractionState | null): NotExtracted =>
  state === null ? null : state.in_progress ? "in_progress" : state.overdue ? "overdue" : null;

type Outcome = "done" | "failed" | "slow" | "too_soon" | "no_transcript" | "not_asked" | null;

/**
 * What became of this meeting's extraction, and the button that runs it again
 * (the user, 2026-10-06).
 *
 * An extraction that failed used to leave an empty board that read as "this
 * meeting produced nothing". Now the screen says it failed: while the server
 * is still trying by itself, and when it has stopped after three tries.
 *
 * "다시 추출" is offered on any meeting, failed or not. The request is
 * accepted at once and run by the worker within a minute, so the screen asks
 * again every few seconds until the run has left a trace -- a new extraction
 * time, or one more failure -- and then tells its parent to read the board
 * again. The run is the automatic one: a meeting whose items a person has
 * edited keeps its item list, which is said under the button so that nobody
 * presses it expecting their corrections to be replaced.
 *
 * **A first run that is not in yet is said, and watched** (the user, dev,
 * 2026-10-08: no items and no decisions minutes after a transcription, both
 * there after "다시 추출" -- the run was still going, and an empty board said
 * the meeting had produced nothing). While the server reports `in_progress`
 * the screen looks again by itself and has the board read when the run is in.
 * The server ends that state: with the run, with a failure, or half an hour
 * after the transcript, when it reports `overdue` and the screen says the
 * extraction has not happened. A run that read no line for want of consent on
 * record says that -- of the meeting, never of a person.
 */
export function ReExtract({
  meetingId,
  onExtracted,
  onState,
}: {
  meetingId: string;
  onExtracted: () => void;
  /** Each state read, for the sentences the empty lists below say. */
  onState?: (state: ExtractionState) => void;
}) {
  const [state, setState] = useState<ExtractionState | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [looked, setLooked] = useState({ times: 0, missed: 0 });
  const alive = useRef(true);
  // The parent's callbacks are new functions on every render of it; a ref
  // keeps them out of the effects' dependencies.
  const told = useRef({ onExtracted, onState });
  told.current = { onExtracted, onState };

  useEffect(() => {
    alive.current = true;
    getExtractionState(meetingId)
      .then((read) => alive.current && setState(read))
      .catch(() => undefined); // nothing to say: the board below has its own error line
    return () => {
      alive.current = false;
    };
  }, [meetingId]);

  useEffect(() => {
    if (state !== null) told.current.onState?.(state);
  }, [state]);

  // The first run, which nobody on this screen asked for. Each read counts
  // one look, which is what asks for the next; a requested run has its own
  // watch below.
  const firstRun =
    state !== null && state.in_progress && !waiting && looked.missed < MISS_LIMIT;
  useEffect(() => {
    if (!firstRun) return;
    const timer = window.setTimeout(() => {
      getExtractionState(meetingId)
        .then((read) => {
          if (!alive.current) return;
          setLooked((so) => ({ times: so.times + 1, missed: 0 }));
          setState(read);
          if (!read.in_progress && read.extracted_at !== null) told.current.onExtracted();
        })
        .catch(() => {
          if (!alive.current) return;
          setLooked((so) => ({ times: so.times + 1, missed: so.missed + 1 }));
        });
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [meetingId, firstRun, looked]);

  const watch = (before: ExtractionState, tries: number) => {
    window.setTimeout(() => {
      if (!alive.current) return;
      getExtractionState(meetingId)
        .then((read) => {
          if (!alive.current) return;
          setState(read);
          const ran =
            !read.requested &&
            (read.extracted_at !== before.extracted_at || read.failures !== before.failures);
          if (ran) {
            setWaiting(false);
            setOutcome(read.failures > before.failures ? "failed" : "done");
            if (read.failures <= before.failures) onExtracted();
          } else if (tries + 1 >= POLL_LIMIT) {
            setWaiting(false);
            setOutcome("slow");
          } else watch(before, tries + 1);
        })
        .catch(() => {
          if (!alive.current) return;
          if (tries + 1 >= POLL_LIMIT) {
            setWaiting(false);
            setOutcome("slow");
          } else watch(before, tries + 1);
        });
    }, POLL_MS);
  };

  const ask = async () => {
    setWaiting(true);
    setOutcome(null);
    try {
      const accepted = await requestExtraction(meetingId);
      if (!alive.current) return;
      const before = state ?? { ...accepted, requested: false };
      setState(accepted);
      watch(before, 0);
    } catch (caught) {
      if (!alive.current) return;
      setWaiting(false);
      setOutcome(
        caught instanceof ApiError && caught.status === 429
          ? "too_soon"
          : caught instanceof ApiError && caught.status === 409
            ? "no_transcript"
            : "not_asked",
      );
    }
  };

  if (state === null) return null;

  const muted = { fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" } as const;
  const critical = { ...muted, color: "var(--color-signal-critical)" } as const;
  const failed = state.failures > 0 && outcome !== "done";

  return (
    <div className="flex flex-col gap-1.5">
      {failed && !waiting ? (
        state.not_published ? (
          // The rows below are this run's: what failed is telling the other
          // analyses, so "could not extract" would be false (PARK, #868).
          state.will_retry ? (
            <p role="status" style={muted}>
              아래 액션 아이템과 결정은 추출되었습니다. 이 결과를 회의 연결과 리포트 분석에
              전달하지 못해 자동으로 다시 시도하고 있습니다({state.failures}번 실패).
            </p>
          ) : (
            <p role="alert" style={critical}>
              아래 액션 아이템과 결정은 추출되었지만, 이 결과를 회의 연결과 리포트 분석에 전달하지
              못했습니다({state.failures}번 시도). 자동으로는 더 시도하지 않습니다. 아래 버튼으로
              다시 시도할 수 있습니다.
            </p>
          )
        ) : state.will_retry ? (
          <p role="status" style={muted}>
            이 회의의 액션 아이템과 결정을 추출하지 못해 자동으로 다시 시도하고 있습니다
            ({state.failures}번 실패). 잠시 뒤 새로 고쳐 주세요.
          </p>
        ) : (
          <p role="alert" style={critical}>
            이 회의의 액션 아이템과 결정을 추출하지 못했습니다({state.failures}번 시도). 자동으로는
            더 시도하지 않습니다. 아래 버튼으로 다시 시도할 수 있습니다.
          </p>
        )
      ) : waiting ? null : state.in_progress ? (
        <p role="status" style={muted}>
          이 회의의 액션 아이템과 결정을 추출하고 있습니다. 보통 1~2분 걸리며, 끝나면 이 화면에
          나타납니다.
        </p>
      ) : state.overdue ? (
        <p role="status" style={muted}>
          전사는 끝났지만 이 회의의 액션 아이템과 결정은 아직 추출되지 않았습니다. 아래 버튼으로
          추출할 수 있습니다.
        </p>
      ) : state.read_nothing ? (
        <p role="status" style={muted}>
          녹음 동의가 기록되지 않아 이 회의의 발화를 읽지 않았습니다. 그래서 추출된 액션 아이템과
          결정이 없습니다. 동의가 기록되면 10분 안에 자동으로 다시 추출합니다.
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          tone="text"
          size="compact"
          type="button"
          disabled={waiting}
          onClick={() => void ask()}
        >
          액션·결정 다시 추출
        </Button>
        <span style={muted}>
          {waiting
            ? "다시 추출을 요청했습니다. 보통 1~2분 안에 반영됩니다."
            : "항목을 고친 적이 있는 회의는 액션 목록을 그대로 두고, 분류와 결정만 새로 만듭니다."}
        </span>
      </div>
      {outcome === "done" ? (
        <p role="status" style={muted}>
          다시 추출했습니다.
        </p>
      ) : outcome === "failed" ? (
        <p role="alert" style={critical}>
          다시 추출하지 못했습니다. 잠시 뒤 다시 시도해 주세요.
        </p>
      ) : outcome === "slow" ? (
        <p role="status" style={muted}>
          아직 끝나지 않았습니다. 잠시 뒤 이 화면을 새로 고쳐 주세요.
        </p>
      ) : outcome === "too_soon" ? (
        <p role="status" style={muted}>
          방금 요청한 추출이 진행 중입니다. 잠시 뒤 다시 눌러 주세요.
        </p>
      ) : outcome === "no_transcript" ? (
        <p role="status" style={muted}>
          아직 전사된 내용이 없어 추출할 수 없습니다.
        </p>
      ) : outcome === "not_asked" ? (
        <p role="alert" style={critical}>
          다시 추출을 요청하지 못했습니다. 잠시 뒤 다시 시도해 주세요.
        </p>
      ) : null}
    </div>
  );
}
