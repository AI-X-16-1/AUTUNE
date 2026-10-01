"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** How many recent input levels the waveform keeps. Matches the rail. */
const LEVEL_WINDOW = 48;

/**
 * What the browser has told us about microphone access.
 *
 * `denied` is its own state rather than an error string because S10 draws it
 * differently: a "권한 허용" action and guidance, not a generic failure.
 */
export type MicrophonePermission = "unknown" | "granted" | "denied";

export type AudioInput = { deviceId: string; label: string };

export type Microphone = {
  /** The stream handed to recording. `null` while only previewing. */
  stream: MediaStream | null;
  levels: number[];
  error: string | null;
  permission: MicrophonePermission;
  /** Audio inputs the browser lists. Labels are empty until permission. */
  devices: AudioInput[];
  /** The chosen input; `""` means the browser's default. */
  deviceId: string;
  /** The meter is running on an open stream that is not recording yet. */
  previewing: boolean;
  selectDevice: (deviceId: string) => void;
  /** Open the selected input for the level meter only. */
  preview: () => Promise<void>;
  /** Hand the selected input to recording, reusing the preview if open. */
  start: () => Promise<void>;
  stop: () => void;
};

const DENIED_ERRORS = new Set(["NotAllowedError", "SecurityError", "PermissionDeniedError"]);

/**
 * The microphone, and a level meter on it.
 *
 * One `MediaStream`, shared by everything that listens: the worklet that
 * sends PCM to the server, the `MediaRecorder` that keeps the recording, and
 * the analyser here that feeds the waveform. Opening it twice would ask the
 * person for permission twice.
 *
 * Two steps, so S10 can show a level before anything records: `preview()`
 * opens the selected input and runs the meter but leaves `stream` null, which
 * is what keeps `useLiveSession` from starting; `start()` publishes that same
 * stream as `stream`. The device the person picked is therefore the device
 * that records — `start()` never reopens a preview it can reuse. Changing the
 * device while previewing reopens on the new one; while recording it only
 * remembers the choice.
 *
 * Cleanup runs on unmount only, because a cleanup keyed on stop's identity
 * would run on the very state change that start() causes.
 */
export function useMicrophone(): Microphone {
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [levels, setLevels] = useState<number[]>(() => Array(LEVEL_WINDOW).fill(0));
  const [error, setError] = useState<string | null>(null);
  const [permission, setPermission] = useState<MicrophonePermission>("unknown");
  const [devices, setDevices] = useState<AudioInput[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const context = useRef<AudioContext | null>(null);
  const timer = useRef<number | null>(null);
  const media = useRef<MediaStream | null>(null);
  const recording = useRef(false);
  const chosen = useRef("");
  // Serialises open/close so a device switch racing a start cannot leave two
  // streams open.
  const opening = useRef<Promise<MediaStream | null> | null>(null);
  // A preview was asked for and has not been ended by start() or stop(): a
  // grant reported later should reopen it.
  const wantsPreview = useRef(false);

  const refreshDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      setDevices(
        all
          .filter((device) => device.kind === "audioinput")
          .map((device, index) => ({
            deviceId: device.deviceId,
            label: device.label || `마이크 ${index + 1}`,
          })),
      );
    } catch {
      // Listing is a convenience; the default input still works without it.
    }
  }, []);

  const release = useCallback(() => {
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
    media.current?.getTracks().forEach((track) => track.stop());
    void context.current?.close().catch(() => {});
    context.current = null;
    media.current = null;
  }, []);

  const open = useCallback(
    async (wanted: string): Promise<MediaStream | null> => {
      release();
      try {
        const opened = await navigator.mediaDevices.getUserMedia({
          audio: wanted ? { deviceId: { exact: wanted } } : true,
          video: false,
        });
        const ctx = new AudioContext();
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        ctx.createMediaStreamSource(opened).connect(analyser);
        const buffer = new Float32Array(analyser.fftSize);
        timer.current = window.setInterval(() => {
          analyser.getFloatTimeDomainData(buffer);
          let sum = 0;
          for (const v of buffer) sum += v * v;
          const rms = Math.min(1, Math.sqrt(sum / buffer.length) * 4);
          setLevels((prev) => [...prev.slice(1), rms]);
        }, 100);
        context.current = ctx;
        media.current = opened;
        setPermission("granted");
        setError(null);
        if (!wanted) {
          // Show the default input as the selected one, now that it has a name.
          const actual = opened.getAudioTracks()[0]?.getSettings().deviceId;
          if (actual) {
            chosen.current = actual;
            setDeviceId(actual);
          }
        }
        void refreshDevices();
        return opened;
      } catch (caught) {
        const name = caught instanceof DOMException ? caught.name : "";
        if (DENIED_ERRORS.has(name)) {
          setPermission("denied");
          setError("마이크 권한이 거부되어 있습니다.");
        } else if (name === "NotFoundError" || name === "OverconstrainedError") {
          setError("선택한 마이크를 찾을 수 없습니다. 다른 입력 장치를 골라 주세요.");
        } else {
          setError("마이크를 열 수 없습니다. 브라우저의 마이크 권한을 확인해 주세요.");
        }
        return null;
      }
    },
    [release, refreshDevices],
  );

  const openSerial = useCallback(
    (wanted: string) => {
      const previous = opening.current ?? Promise.resolve(null);
      const next = previous.then(() => open(wanted));
      opening.current = next;
      return next;
    },
    [open],
  );

  const preview = useCallback(async () => {
    if (recording.current) return;
    wantsPreview.current = true;
    const opened = await openSerial(chosen.current);
    if (!recording.current) setPreviewing(opened !== null);
  }, [openSerial]);

  const previewRef = useRef(preview);
  previewRef.current = preview;

  const start = useCallback(async () => {
    // Already recording: never ask for a second stream while one is live,
    // which would leak the first.
    if (recording.current) return;
    if (opening.current) await opening.current;
    const opened = media.current ?? (await openSerial(chosen.current));
    if (!opened) return;
    recording.current = true;
    wantsPreview.current = false;
    setPreviewing(false);
    setStream(opened);
  }, [openSerial]);

  const selectDevice = useCallback(
    (next: string) => {
      chosen.current = next;
      setDeviceId(next);
      if (!recording.current && media.current) void preview();
    },
    [preview],
  );

  const stop = useCallback(() => {
    release();
    recording.current = false;
    wantsPreview.current = false;
    setPreviewing(false);
    setStream(null);
  }, [release]);

  // Ask the browser what it already knows, so a denial shows as one before
  // anybody presses anything, and a grant from the site settings is noticed.
  useEffect(() => {
    let status: PermissionStatus | null = null;
    let current = true;
    const apply = () => {
      if (!status || !current) return;
      if (status.state === "denied") setPermission("denied");
      else if (status.state === "granted") {
        setPermission("granted");
        if (wantsPreview.current && !media.current && !recording.current) {
          void previewRef.current();
        }
      } else setPermission("unknown");
    };
    navigator.permissions
      ?.query({ name: "microphone" as PermissionName })
      .then((result) => {
        status = result;
        apply();
        result.addEventListener("change", apply);
      })
      .catch(() => {
        // Not every browser can be asked; getUserMedia's answer will do.
      });
    void refreshDevices();
    navigator.mediaDevices?.addEventListener?.("devicechange", refreshDevices);
    return () => {
      current = false;
      status?.removeEventListener("change", apply);
      navigator.mediaDevices?.removeEventListener?.("devicechange", refreshDevices);
    };
  }, [refreshDevices]);

  const stopRef = useRef(stop);
  stopRef.current = stop;
  useEffect(() => () => stopRef.current(), []);

  return {
    stream,
    levels,
    error,
    permission,
    devices,
    deviceId,
    previewing,
    selectDevice,
    preview,
    start,
    stop,
  };
}
