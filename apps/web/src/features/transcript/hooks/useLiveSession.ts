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

  const teardownAudio = useCallback(() => {
    worklet.current?.disconnect();
    worklet.current = null;
    void context.current?.close();
    context.current = null;
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
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
        // A server that drops the connection mid-`stop()` will never send
        // `ended` afterward -- do not make the wait run out the clock.
        ended.current?.();
        if (!readyResolved && event.code !== 4503) {
          const message = CLOSE_MESSAGES[event.code] ?? `실시간 전사 연결이 끊겼습니다 (${event.code}).`;
          reject(Object.assign(new Error(message), { code: event.code }));
        }
        socket.current = null;
        // Any drop once the session is live -- except one we asked for --
        // leaves the recording running without the live view.
        if (readyResolved && !stopping.current) setLiveLost(true);
      };
      ws.onerror = () => reject(new Error("라이브 전사 서버에 연결할 수 없습니다. 녹음은 계속됩니다."));
    });

    // The recorder starts regardless of what the socket does: the recording
    // must not depend on the live view.
    const rec = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
    rec.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.current.push(event.data);
    };
    recorder.current = rec;
    rec.start(1000);
    timer.current = window.setInterval(() => setElapsed((s) => s + 1), 1000);

    try {
      await ready;
    } catch (caught) {
      // A concurrent stop() (or a fresh start()) may already have closed or
      // replaced this socket -- e.g. stop() closing a still-connecting
      // socket rejects this same `ready`. A stale rejection must not
      // overwrite state something else already set.
      if (socket.current !== ws) return;
      const code = closeCodeOf(caught);
      const message = caught instanceof Error ? caught.message : String(caught);
      if (code !== undefined && REFUSAL_CODES.has(code)) {
        // Not authorised, or the meeting refused this session outright: stop
        // the recording rather than keep one nobody will ever see.
        if (timer.current !== null) window.clearInterval(timer.current);
        timer.current = null;
        if (rec.state !== "inactive") rec.stop();
        recorder.current = null;
        chunks.current = [];
        socket.current = null;
        setError(message);
        setPhase("error");
        return;
      }
      // A transport failure, or the model is down (4503): the person is
      // still authorised, so the recording continues without the live view.
      setLiveLost(true);
      setError(message);
      setPhase("recording"); // recording without the live view
      return;
    }

    // A stop() -- or a fresh start() -- may already have run while this one
    // was still connecting. Do not resurrect a session nobody is waiting for.
    if (socket.current !== ws) return;

    const ctx = new AudioContext();
    await ctx.audioWorklet.addModule("/pcm-worklet.js");
    const node = new AudioWorkletNode(ctx, "pcm-16k");
    node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(event.data);
    };
    ctx.createMediaStreamSource(stream).connect(node);
    context.current = ctx;
    worklet.current = node;
    setPhase("recording");
  }, [meetingId, stream]);

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
    teardownAudio();

    // 2. Stop the recorder and assemble the blob.
    const rec = recorder.current;
    if (rec && rec.state !== "inactive") {
      await new Promise<void>((resolve) => {
        rec.onstop = () => resolve();
        rec.stop();
      });
    }
    recorder.current = null;
    blob.current = new Blob(chunks.current, { type: "audio/webm" });
    chunks.current = [];

    // 3. Upload.
    await upload();
  }, [teardownAudio, upload]);

  useEffect(() => {
    return () => {
      teardownAudio();
      socket.current?.close();
    };
  }, [teardownAudio]);

  return { phase, rows, elapsedSeconds, liveLost, error, start, pause, resume, stop, retryUpload: upload };
}
