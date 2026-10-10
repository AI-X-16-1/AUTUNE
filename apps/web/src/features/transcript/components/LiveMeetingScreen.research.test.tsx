import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LiveMeetingScreen } from "./LiveMeetingScreen";
import type { LivePhase, LiveSession } from "../hooks/useLiveSession";
import type { Microphone } from "../hooks/useMicrophone";
import type { LiveRow } from "../types";

// Live research is covered by the consent attested at the gate (#1162): a
// recording started with the box unticked sends no row and offers no 조사.

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../hooks/useMeetingTitle", () => ({ useMeetingTitle: () => "주간 회의" }));

const attest = vi.fn<(meetingId: string) => Promise<void>>();
vi.mock("../api", () => ({
  attestConsent: (meetingId: string) => attest(meetingId),
  // The top bar's own title read; left unanswered, it draws no title.
  getMeeting: () => new Promise(() => undefined),
  // The 회의 중 조사 panel's read; nothing looked up yet.
  listLiveResearch: () => Promise.resolve([]),
  detectLive: () => detect(),
}));
const detect = vi.fn(() => Promise.resolve({ queued: true }));

const start = vi.fn<() => Promise<void>>();
const microphone: Microphone = {
  stream: null,
  levels: [],
  error: null,
  permission: "granted",
  devices: [],
  deviceId: "",
  previewing: true,
  selectDevice: vi.fn(),
  preview: vi.fn(() => Promise.resolve()),
  start: () => start(),
  stop: vi.fn(),
};
vi.mock("../hooks/useMicrophone", () => ({ useMicrophone: () => microphone }));

let phase: LivePhase = "idle";
let rows: LiveRow[] = [];
vi.mock("../hooks/useLiveSession", () => ({
  useLiveSession: (): LiveSession => ({
    phase,
    rows,
    elapsedSeconds: 0,
    liveLost: false,
    error: null,
    start: vi.fn(() => Promise.resolve()),
    pause: vi.fn(),
    resume: vi.fn(),
    stop: vi.fn(() => Promise.resolve()),
    retryUpload: vi.fn(() => Promise.resolve()),
    saveRecording: vi.fn(),
    reset: vi.fn(),
  }),
}));

afterEach(() => {
  cleanup();
  attest.mockReset();
  start.mockReset();
  detect.mockClear();
  phase = "idle";
  rows = [];
});

const row = (i: number) =>
  ({
    utterance: {
      id: `utt_live_${i}`,
      speaker: "화자 1",
      speaker_id: null,
      role: null,
      start: i,
      end: i + 1,
      text: `말 ${i}`,
      confidence: 1,
    },
  }) as LiveRow;

const consent = () =>
  screen.getByRole("checkbox", { name: /참석자 전원이 녹음과 분석에 동의/ });

async function record(tick: boolean) {
  attest.mockResolvedValue(undefined);
  start.mockResolvedValue(undefined);
  const view = render(<LiveMeetingScreen meetingId="mtg_1" />);
  if (tick) {
    fireEvent.click(consent());
    await waitFor(() => expect((consent() as HTMLInputElement).checked).toBe(true));
  }
  fireEvent.click(screen.getByRole("button", { name: "녹음 시작" }));
  await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
  phase = "recording";
  rows = Array.from({ length: 6 }, (_, i) => row(i));
  view.rerender(<LiveMeetingScreen meetingId="mtg_1" />);
  await act(async () => {});
}

describe("LiveMeetingScreen, live research and consent", () => {
  it("offers 조사 and sends rows once consent was ticked", async () => {
    await record(true);

    expect(screen.getAllByRole("button", { name: /줄 조사$/ })).toHaveLength(6);
    expect(screen.getByRole("region", { name: "회의 중 조사" })).toBeTruthy();
    expect(detect).toHaveBeenCalledTimes(1);
  });

  it("offers no 조사 and sends nothing when the box was left unticked", async () => {
    await record(false);

    expect(screen.queryAllByRole("button", { name: /줄 조사$/ })).toHaveLength(0);
    expect(screen.queryByRole("region", { name: "회의 중 조사" })).toBeNull();
    expect(detect).not.toHaveBeenCalled();
  });
});
