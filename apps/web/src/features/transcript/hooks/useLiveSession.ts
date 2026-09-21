"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { Utterance } from "@autune/contracts";

import { getToken, liveSocketUrl, uploadRecording } from "../api";
import type { LiveRow } from "../types";

export type LivePhase =
  | "idle"
  | "connecting"
  | "recording"
  | "paused"
  | "uploading"
  | "done"
  | "error"
  | "upload_failed";

export type LiveSession = {
  phase: LivePhase;
  rows: LiveRow[];
  elapsedSeconds: number;
  liveLost: boolean;
  error: string | null;
  start: () => Promise<void>;
  pause: () => void;
  resume: () => void;
  stop: () => Promise<void>;
  retryUpload: () => Promise<void>;
  /** Clears a refusal so the gate can try `start()` again. Only valid from `"error"`. */
  reset: () => void;
};

type ServerMessage =
  | { type: "ready" }
  | { type: "row"; utterance: Utterance }
  | { type: "error"; code: string }
  | { type: "ended" };

const CLOSE_MESSAGES: Record<number, string> = {
  4401: "로그인 토큰이 없거나 만료되었습니다.",
  4403: "이 회의의 팀 멤버가 아닙니다.",
  4404: "회의를 찾을 수 없습니다.",
  4409: "이 회의는 이미 다른 곳에서 녹음 중입니다.",
  4503: "서버의 전사 모델을 불러올 수 없습니다. 녹음은 계속됩니다.",
};

/** Authorisation/precondition failures: no live view will ever arrive for
 * this session, so the recording is stopped rather than kept blind. */
const REFUSAL_CODES = new Set<number>([4401, 4403, 4404, 4409]);

/** The close code on a `ready`-rejection, if the rejection came from a close
 * event rather than a transport error (which carries none). */
function closeCodeOf(error: unknown): number | undefined {
  if (error instanceof Error && "code" in error) {
    const code = (error as Error & { code?: unknown }).code;
    if (typeof code === "number") return code;
  }
  return undefined;
}

/**
 * The live channel, the recording, and the hand-off between them.
 *
 * Two things listen to the same `stream`: an `AudioWorklet` that sends PCM
 * frames over the socket for display, and a `MediaRecorder` that keeps the
 * whole recording for the upload. **They do not depend on each other** once
 * the session is live: if the socket drops after `ready`, `liveLost` goes
 * true, the rows stop, and the recorder keeps going; `stop()` still uploads.
 * Losing the live view for a while and losing the recording are different
 * orders of failure, and only the second is prevented here (design, section
 * 4.1).
 *
 * A failure *before* `ready` is not that case. A refusal -- no token, not a
 * team member, no such meeting, or someone else already recording this
 * meeting (4401/4403/4404/4409) -- means no live view is ever coming, so the
 * recorder is stopped and the session lands on `phase: "error"` instead of
 * recording something nobody asked for. A transport failure (the socket
 * never connects at all) or the model being unavailable (4503) leaves the
 * person still authorised, so those degrade to `liveLost` + recording-only,
 * the same as a mid-session drop.
 *
 * `stop()` waits for the server's `ended` -- the last utterance's row arrives
 * before it -- then stops the recorder, uploads the blob, and resolves. The
 * caller navigates. A failed upload keeps the blob in memory for
 * `retryUpload()`.
 */
export function useLiveSession(meetingId: string, stream: MediaStream | null): LiveSession {
  const [phase, setPhase] = useState<LivePhase>("idle");
  const [rows, setRows] = useState<LiveRow[]>([]);
  const [elapsedSeconds, setElapsed] = useState(0);
  const [liveLost, setLiveLost] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const socket = useRef<WebSocket | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const worklet = useRef<AudioWorkletNode | null>(null);
  const context = useRef<AudioContext | null>(null);
  const timer = useRef<number | null>(null);
  const ended = useRef<(() => void) | null>(null);
  const blob = useRef<Blob | null>(null);
  const stopping = useRef(false);
  /** Bumped by every `start()`, `stop()`, and `abandon()`. A handler closed
   * over in `start()` compares its own generation against this ref to tell
   * whether it still belongs to the session everyone else is looking at --
   * unlike a boolean, a monotonic counter cannot be reset out from under an
   * old session by whatever runs next. */
  const generation = useRef(0);

  const teardownAudio = useCallback(() => {
    worklet.current?.disconnect();
    worklet.current = null;
    void context.current?.close();
    context.current = null;
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
  }, []);

  /**
   * Give up on whatever recording is in flight: stop the `MediaRecorder` if
   * it is running, discard the audio collected so far, and drop the socket
   * reference. Used where there is nobody left to upload to -- a refused
   * session (no live view is ever coming) and an unmount (the component is
   * gone, so there is no `stop()` call coming either).
   *
   * Bumps `generation` too: whatever socket this abandoned still has an
   * `onclose`/`onerror` in flight (unmount closes it but does not wait for
   * the event), and that late rejection -- and any stray final chunk from
   * the recorder this just stopped -- must read as belonging to a session
   * nobody is looking at anymore, even once a fresh `start()` has taken over
   * both refs.
   */
  const abandon = useCallback(() => {
    generation.current++;
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
    if (recorder.current && recorder.current.state !== "inactive") {
      recorder.current.ondataavailable = null;
      recorder.current.stop();
    }
    recorder.current = null;
    chunks.current = [];
    socket.current = null;
  }, []);

  const upload = useCallback(async () => {
    if (!blob.current) return;
    setPhase("uploading");
    try {
      await uploadRecording(meetingId, blob.current);
      blob.current = null;
      setPhase("done");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "업로드에 실패했습니다.");
      setPhase("upload_failed");
    }
  }, [meetingId]);

  const start = useCallback(async () => {
    // Already connecting or connected: never open a second socket/recorder
    // on the same stream (e.g. a double-invoke in dev).
    if (socket.current || recorder.current) return;
    // This call's identity. Every handler below, and both continuations
    // after `await ready`, check this against `generation.current` before
    // doing anything -- if a `stop()` or `abandon()` (or a newer `start()`)
    // has run since, it is no longer current, no matter what socket-level
    // event triggers it.
    const mine = ++generation.current;
    if (!stream) return;
    const token = getToken();
    if (!token) {
      setError("로그인 토큰이 없습니다. environments.md의 dev token 절을 보세요.");
      setPhase("error");
      return;
    }
    setPhase("connecting");
    setError(null);
    setLiveLost(false);
    setRows([]);
    setElapsed(0);
    chunks.current = [];
    stopping.current = false;

    const ws = new WebSocket(liveSocketUrl(meetingId));
    ws.binaryType = "arraybuffer";
    socket.current = ws;

    let readyResolved = false;
    const ready = new Promise<void>((resolve, reject) => {
      ws.onopen = () => ws.send(JSON.stringify({ type: "hello", token }));
      ws.onmessage = (event: MessageEvent<string>) => {
        if (generation.current !== mine) return;
        const message = JSON.parse(event.data) as ServerMessage;
        if (message.type === "ready") {
          readyResolved = true;
          resolve();
        } else if (message.type === "row") {
          setRows((prev) => [...prev, { utterance: message.utterance }]);
        } else if (message.type === "ended") {
          ended.current?.();
        } else if (message.type === "error" && message.code === "model_unavailable") {
          reject(Object.assign(new Error(CLOSE_MESSAGES[4503]), { code: 4503 }));
        }
      };
      ws.onclose = (event) => {
        // The `ended` wait belongs to whichever socket is still current,
        // not to a generation: `stop()` awaits it while this is still
        // `socket.current` and only bumps `generation` afterward, so a
        // server that drops the connection mid-`stop()` (never sending
        // `ended`) must still release the wait for *this* socket. Checking
        // `socket.current === ws` here, before the generation check below,
        // is also what keeps a long-abandoned socket's late close from
        // resolving a completely different, newer session's own wait --
        // that ref no longer points at this `ws` once anything has moved on.
        if (socket.current === ws) ended.current?.();
        if (generation.current !== mine) return;
        if (!readyResolved && event.code !== 4503) {
          const message = CLOSE_MESSAGES[event.code] ?? `실시간 전사 연결이 끊겼습니다 (${event.code}).`;
          reject(Object.assign(new Error(message), { code: event.code }));
        }
        socket.current = null;
        // Any drop once the session is live -- except one we asked for --
        // leaves the recording running without the live view.
        if (readyResolved && !stopping.current) setLiveLost(true);
      };
      ws.onerror = () => {
        if (generation.current !== mine) return;
        reject(new Error("라이브 전사 서버에 연결할 수 없습니다. 녹음은 계속됩니다."));
      };
    });

    // The recorder starts regardless of what the socket does: the recording
    // must not depend on the live view.
    const rec = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
    rec.ondataavailable = (event) => {
      if (generation.current !== mine) return;
      if (event.data.size > 0) chunks.current.push(event.data);
    };
    recorder.current = rec;
    rec.start(1000);
    timer.current = window.setInterval(() => setElapsed((s) => s + 1), 1000);

    try {
      await ready;
    } catch (caught) {
      // Stale if a newer `start()`, or this session's own `stop()`/
      // `abandon()`, has already bumped the generation -- `socket.current`
      // cannot tell us that: `onclose` nulls it before this handler's
      // microtask runs, so it is `null` (not `=== ws`) for every
      // close-originated rejection, stale or not.
      if (generation.current !== mine) return;
      const code = closeCodeOf(caught);
      const message = caught instanceof Error ? caught.message : String(caught);
      if (code !== undefined && REFUSAL_CODES.has(code)) {
        // Not authorised, or the meeting refused this session outright: stop
        // the recording rather than keep one nobody will ever see.
        abandon();
        setError(message);
        setPhase("error");
        return;
      }
      // A transport failure, or the model is down (4503): the person is
      // still authorised, so the recording continues without the live view.
      setLiveLost(true);
      setError(message);
      // Recording without the live view -- unless a pause() already ran.
      setPhase((p) => (p === "connecting" ? "recording" : p));
      return;
    }

    // A stop() -- or a fresh start() -- may already have run while this one
    // was still connecting. Do not resurrect a session nobody is waiting for.
    if (generation.current !== mine) return;

    let ctx: AudioContext | null = null;
    try {
      ctx = new AudioContext();
      await ctx.audioWorklet.addModule("/pcm-worklet.js");
      // addModule fetches the worklet. A stop() or unmount in that window
      // must not let this continuation wire the microphone into a session
      // nobody is running.
      if (generation.current !== mine) {
        void ctx.close();
        return;
      }
      const node = new AudioWorkletNode(ctx, "pcm-16k");
      node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(event.data);
      };
      ctx.createMediaStreamSource(stream).connect(node);
      context.current = ctx;
      worklet.current = node;
    } catch {
      void ctx?.close();
      if (generation.current !== mine) return;
      // The worklet could not be loaded or wired: no PCM will reach the
      // server, so no rows are coming. The recorder does not need it.
      setLiveLost(true);
      setError("라이브 전사 오디오를 준비하지 못했습니다. 녹음은 계속됩니다.");
    }
    // A pause() that ran while this was still connecting is not undone.
    setPhase((p) => (p === "connecting" ? "recording" : p));
  }, [meetingId, stream, abandon]);

  const pause = useCallback(() => {
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({ type: "pause" }));
    }
    recorder.current?.pause();
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
    setPhase("paused");
  }, []);

  const resume = useCallback(() => {
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({ type: "resume" }));
    }
    recorder.current?.resume();
    timer.current = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    setPhase("recording");
  }, []);

  const stop = useCallback(async () => {
    // Already stopping (or stopped): a second call must not send a second
    // `stop`, wait a second time, or upload an already-emptied blob.
    if (stopping.current) return;
    stopping.current = true;

    // 1. If connected, tell the server and wait for `ended` (the last row
    //    comes first). Close the socket in whatever state it is afterward --
    //    one left open mid-handshake must not let a late `ready` resurrect a
    //    session that has already been told to stop.
    if (socket.current?.readyState === WebSocket.OPEN) {
      const finished = new Promise<void>((resolve) => {
        let timeoutId: number | null = null;
        const done = () => {
          if (timeoutId !== null) window.clearTimeout(timeoutId);
          ended.current = null;
          resolve();
        };
        ended.current = done;
        timeoutId = window.setTimeout(done, 15_000); // a server that never answers must not hold the upload
      });
      socket.current.send(JSON.stringify({ type: "stop" }));
      await finished;
    }
    socket.current?.close(1000);
    socket.current = null;
    // This session's socket is done; anything it or a stray late event
    // still emits from here on belongs to nobody.
    generation.current++;
    teardownAudio();

    // 2. Stop the recorder and assemble the blob.
    const rec = recorder.current;
    if (rec && rec.state !== "inactive") {
      await new Promise<void>((resolve) => {
        // Reassign rather than reuse start()'s closure: MediaRecorder.stop()
        // always flushes one last `dataavailable`, and the generation bump
        // just above would make that closure -- gated on the generation --
        // reject it as stale. This final chunk is still ours; only a
        // *different* session's leftovers should be discarded that way.
        rec.ondataavailable = (event) => {
          if (event.data.size > 0) chunks.current.push(event.data);
        };
        rec.onstop = () => resolve();
        rec.stop();
      });
    }
    recorder.current = null;
    if (chunks.current.length === 0) {
      // Nothing was recorded (the recorder never produced a chunk): there is
      // no upload to make, and an empty blob is not a recording.
      blob.current = null;
      setError("업로드할 녹음이 없습니다.");
      setPhase("error");
      return;
    }
    blob.current = new Blob(chunks.current, { type: "audio/webm" });
    chunks.current = [];

    // 3. Upload.
    await upload();
  }, [teardownAudio, upload]);

  useEffect(() => {
    return () => {
      teardownAudio();
      socket.current?.close();
      // Nobody is left to call stop(): stop the recorder and drop both
      // refs so a StrictMode remount's start() is not blocked by the
      // re-entry guard, and so a recorder from an unmounted session is
      // never left running.
      abandon();
    };
  }, [teardownAudio, abandon]);

  // Only a refusal (phase "error") leaves nothing running -- abandon() already
  // cleared every ref when start() rejected -- so this only needs to clear the
  // state a fresh start() will not otherwise reset before it, letting the gate
  // try again.
  const reset = useCallback(() => {
    if (phase !== "error") return;
    setPhase("idle");
    setError(null);
    setLiveLost(false);
    setRows([]);
    setElapsed(0);
  }, [phase]);

  return { phase, rows, elapsedSeconds, liveLost, error, start, pause, resume, stop, retryUpload: upload, reset };
}
