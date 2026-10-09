import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LiveMeetingScreen } from "./LiveMeetingScreen";
import type { LivePhase, LiveSession } from "../hooks/useLiveSession";
import type { Microphone } from "../hooks/useMicrophone";

// The slot above the gate's consent row. This screen does not know what the
// page puts there; what is pinned is where it is drawn, that it changes
// nothing else, and that consent and "녹음 시작" work with it there.

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../hooks/useMeetingTitle", () => ({ useMeetingTitle: () => "주간 회의" }));

const attest = vi.fn<(meetingId: string) => Promise<void>>();
vi.mock("../api", () => ({
  attestConsent: (meetingId: string) => attest(meetingId),
  // The top bar's own title read; left unanswered, it draws no title.
  getMeeting: () => new Promise(() => undefined),
  // The 회의 중 조사 panel's read; nothing looked up yet.
  listLiveResearch: () => Promise.resolve([]),
}));

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
vi.mock("../hooks/useLiveSession", () => ({
  useLiveSession: (): LiveSession => ({
    phase,
    rows: [],
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
  phase = "idle";
});

const NOTICE = <p role="note">the notice</p>;
const consent = () =>
  screen.getByRole("checkbox", { name: /참석자 전원이 녹음과 분석에 동의/ });

describe("LiveMeetingScreen, the notice slot", () => {
  it("draws what the page hands it directly above the consent row of the gate", () => {
    render(<LiveMeetingScreen meetingId="mtg_1" notice={NOTICE} />);

    const note = screen.getByRole("note");
    expect(note.nextElementSibling).toBe(consent().closest("label"));
  });

  it("is the gate as it was when the page hands it nothing", () => {
    const withIt = render(<LiveMeetingScreen meetingId="mtg_1" notice={NOTICE} />);
    withIt.getByRole("note").remove();
    const withItRemoved = withIt.container.innerHTML;
    withIt.unmount();

    const without = render(<LiveMeetingScreen meetingId="mtg_1" />);

    expect(without.queryByRole("note")).toBeNull();
    expect(without.container.innerHTML).toBe(withItRemoved);
  });

  it("belongs to the gate: once the recording runs it is not drawn", () => {
    phase = "recording";
    render(<LiveMeetingScreen meetingId="mtg_1" notice={NOTICE} />);

    expect(screen.queryByRole("note")).toBeNull();
  });

  it("records consent and starts as before with the notice there", async () => {
    attest.mockResolvedValue(undefined);
    start.mockResolvedValue(undefined);
    render(<LiveMeetingScreen meetingId="mtg_1" notice={NOTICE} />);

    fireEvent.click(consent());
    await waitFor(() => expect((consent() as HTMLInputElement).checked).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "녹음 시작" }));

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
    expect(attest).toHaveBeenCalledWith("mtg_1");
  });

  it("does not gate the start: an unticked box still records, as today", async () => {
    start.mockResolvedValue(undefined);
    render(<LiveMeetingScreen meetingId="mtg_1" notice={NOTICE} />);

    fireEvent.click(screen.getByRole("button", { name: "녹음 시작" }));

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
    expect(attest).not.toHaveBeenCalled();
  });
});
