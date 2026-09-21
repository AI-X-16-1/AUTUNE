"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** How many recent input levels the waveform keeps. Matches the rail. */
const LEVEL_WINDOW = 48;

export type Microphone = {
  stream: MediaStream | null;
  levels: number[];
  error: string | null;
  start: () => Promise<void>;
  stop: () => void;
};

/**
 * The microphone, and a level meter on it.
 *
 * One `MediaStream`, shared by everything that listens: the worklet that
 * sends PCM to the server, the `MediaRecorder` that keeps the recording, and
 * the analyser here that feeds the waveform. Opening it twice would ask the
 * person for permission twice.
 */
export function useMicrophone(): Microphone {
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [levels, setLevels] = useState<number[]>(() => Array(LEVEL_WINDOW).fill(0));
  const [error, setError] = useState<string | null>(null);
  const context = useRef<AudioContext | null>(null);
  const timer = useRef<number | null>(null);

  const start = useCallback(async () => {
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      const ctx = new AudioContext();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      ctx.createMediaStreamSource(media).connect(analyser);
      const buffer = new Float32Array(analyser.fftSize);
      timer.current = window.setInterval(() => {
        analyser.getFloatTimeDomainData(buffer);
        let sum = 0;
        for (const v of buffer) sum += v * v;
        const rms = Math.min(1, Math.sqrt(sum / buffer.length) * 4);
        setLevels((prev) => [...prev.slice(1), rms]);
      }, 100);
      context.current = ctx;
      setStream(media);
      setError(null);
    } catch {
      setError("마이크를 열 수 없습니다. 브라우저의 마이크 권한을 확인해 주세요.");
    }
  }, []);

  const stop = useCallback(() => {
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
    stream?.getTracks().forEach((track) => track.stop());
    void context.current?.close();
    context.current = null;
    setStream(null);
  }, [stream]);

  useEffect(() => stop, [stop]);

  return { stream, levels, error, start, stop };
}
