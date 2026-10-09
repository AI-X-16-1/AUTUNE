import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LiveMeetingScreen } from "./LiveMeetingScreen";
import type { LivePhase, LiveSession } from "../hooks/useLiveSession";
import type { Microphone } from "../hooks/useMicrophone";
import { plannedEndOf, rememberPlannedEnd } from "../plannedEnd";

// S14's small cut (#1147): five minutes before the end this tab was told, the
// screen draws what the page put in `endingSoon`. This screen owns the when
// and the team; what is drawn is not its business.

const push = vi.fn<(href: string) => void>();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("../hooks/useMeetingTitle", () => ({ useMeetingTitle: () => "주간 회의" }));
// The top bar reads the title for itself; kept out so that the only meeting
// read counted below is the one made for the slot.
vi.mock("./LiveTopBar", () => ({ LiveTopBar: () => null }));

const getMeeting = vi.fn<(meetingId: string) => Promise<{ title: string; team_id: string }>>();
vi.mock("../api", () => ({
  attestConsent: vi.fn(),
  getMeeting: (meetingId: string) => getMeeting(meetingId),
}));

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
  start: vi.fn(() => Promise.resolve()),
  stop: vi.fn(),
};
vi.mock("../hooks/useMicrophone", () => ({ useMicrophone: () => microphone }));

let phase: LivePhase = "recording";
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

const MINUTE = 60_000;
const slot = vi.fn((teamId: string) => (
  <p data-testid="end-slot">the page's band for {teamId}</p>
));
// By test id: the screen has an `aside` and status lines of its own.
const band = () => screen.queryByTestId("end-slot");
const endsIn = (ms: number) =>
  rememberPlannedEnd("mtg_1", new Date(Date.now() + ms).toISOString());
/** Let the clock run and every promise it released settle. */
const pass = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-09T05:00:00Z"));
  getMeeting.mockResolvedValue({ title: "주간 회의", team_id: "team_7" });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  getMeeting.mockReset();
  slot.mockClear();
  push.mockReset();
  window.sessionStorage.clear();
  phase = "recording";
});

describe("LiveMeetingScreen, the end-of-meeting slot", () => {
  it("draws the slot five minutes before the planned end and not a second sooner", async () => {
    endsIn(20 * MINUTE);
    render(<LiveMeetingScreen meetingId="mtg_1" endingSoon={slot} />);

    await pass(15 * MINUTE - 1000);
    expect(band()).toBeNull();
    expect(getMeeting).not.toHaveBeenCalled();

    await pass(1000);
    expect(band()?.textContent).toBe("the page's band for team_7");
  });

  it("hands the slot the meeting's own team, read once at that moment", async () => {
    endsIn(6 * MINUTE);
    render(<LiveMeetingScreen meetingId="mtg_1" endingSoon={slot} />);

    await pass(MINUTE);

    expect(getMeeting).toHaveBeenCalledTimes(1);
    expect(getMeeting).toHaveBeenCalledWith("mtg_1");
    expect(slot).toHaveBeenCalledWith("team_7");
  });

  it("draws it at once for a recording that starts inside the last five minutes", async () => {
    endsIn(2 * MINUTE);
    render(<LiveMeetingScreen meetingId="mtg_1" endingSoon={slot} />);

    await pass(0);

    expect(band()).not.toBeNull();
  });

  it("keeps it up when the meeting runs past its planned end", async () => {
    endsIn(6 * MINUTE);
    render(<LiveMeetingScreen meetingId="mtg_1" endingSoon={slot} />);

    await pass(30 * MINUTE);

    expect(band()).not.toBeNull();
  });

  it("draws it above the transcript, and still while the recording is paused", async () => {
    phase = "paused";
    endsIn(2 * MINUTE);
    const { container } = render(<LiveMeetingScreen meetingId="mtg_1" endingSoon={slot} />);

    await pass(0);

    const drawn = band()!;
    expect(drawn).not.toBeNull();
    // Everything after the band in the document is the transcript's.
    expect(drawn.nextElementSibling).not.toBeNull();
    expect(container.contains(drawn)).toBe(true);
  });

  it("times nothing on the gate: a recording that has not started has no alert", async () => {
    phase = "idle";
    endsIn(2 * MINUTE);
    render(<LiveMeetingScreen meetingId="mtg_1" endingSoon={slot} />);

    await pass(30 * MINUTE);

    expect(band()).toBeNull();
    expect(slot).not.toHaveBeenCalled();
    expect(getMeeting).not.toHaveBeenCalled();
  });

  it("never draws it, and never reads the team, for a meeting this tab was told no end of", async () => {
    render(<LiveMeetingScreen meetingId="mtg_1" endingSoon={slot} />);

    await pass(3 * 60 * MINUTE);

    expect(band()).toBeNull();
    expect(slot).not.toHaveBeenCalled();
    expect(getMeeting).not.toHaveBeenCalled();
  });

  it("is another meeting's planned end, not this one's", async () => {
    rememberPlannedEnd("mtg_other", new Date(Date.now() + 2 * MINUTE).toISOString());
    render(<LiveMeetingScreen meetingId="mtg_1" endingSoon={slot} />);

    await pass(30 * MINUTE);

    expect(band()).toBeNull();
  });

  it("is the screen as it was when the page fills no slot, planned end or not", async () => {
    const plain = render(<LiveMeetingScreen meetingId="mtg_1" />);
    await pass(0);
    const before = plain.container.innerHTML;
    plain.unmount();

    endsIn(2 * MINUTE);
    const told = render(<LiveMeetingScreen meetingId="mtg_1" />);
    await pass(30 * MINUTE);

    expect(told.container.innerHTML).toBe(before);
    expect(getMeeting).not.toHaveBeenCalled();
  });

  it("leaves the slot empty when the team cannot be read, and the recording alone", async () => {
    getMeeting.mockRejectedValue(new Error("gone"));
    endsIn(2 * MINUTE);
    render(<LiveMeetingScreen meetingId="mtg_1" endingSoon={slot} />);

    await pass(MINUTE);

    expect(band()).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("forgets the planned end once the recording is in", async () => {
    phase = "done";
    endsIn(2 * MINUTE);
    render(<LiveMeetingScreen meetingId="mtg_1" endingSoon={slot} />);

    await pass(0);

    expect(push).toHaveBeenCalledWith("/meetings/mtg_1");
    expect(plannedEndOf("mtg_1")).toBeNull();
  });
});
