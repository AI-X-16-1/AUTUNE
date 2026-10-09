import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NewMeetingScreen } from "./NewMeetingScreen";

// The slot above the upload's consent row. This screen does not know what the
// page puts there; what is pinned is where it is drawn, that it changes
// nothing else, and that the upload goes through with it there.

const push = vi.fn<(href: string) => void>();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const attest = vi.fn<(meetingId: string) => Promise<void>>();
const upload = vi.fn<(meetingId: string, file: File) => Promise<void>>();
vi.mock("../api", () => ({
  attestConsent: (meetingId: string) => attest(meetingId),
  createMeeting: vi.fn(),
  listTeams: () => Promise.resolve([]),
  uploadRecording: (meetingId: string, file: File) => upload(meetingId, file),
}));

afterEach(() => {
  cleanup();
  push.mockReset();
  attest.mockReset();
  upload.mockReset();
});

const NOTICE = <p role="note">the notice</p>;
const consent = () =>
  screen.getByRole("checkbox", { name: /모든 참석자가 녹음과 분석에 동의/ });

describe("NewMeetingScreen, the notice slot", () => {
  it("draws what the page hands it directly above the consent row", () => {
    render(<NewMeetingScreen existingMeetingId="mtg_1" notice={NOTICE} />);

    const note = screen.getByRole("note");
    expect(note.nextElementSibling).toBe(consent().closest("label"));
  });

  it("is the screen as it was when the page hands it nothing", () => {
    const withIt = render(
      <NewMeetingScreen existingMeetingId="mtg_1" notice={NOTICE} />,
    );
    withIt.getByRole("note").remove();
    const withItRemoved = withIt.container.innerHTML;
    withIt.unmount();

    const without = render(<NewMeetingScreen existingMeetingId="mtg_1" />);

    expect(without.queryByRole("note")).toBeNull();
    expect(without.container.innerHTML).toBe(withItRemoved);
  });

  it("is not drawn on the live path, which has no consent row here", async () => {
    render(<NewMeetingScreen notice={NOTICE} />);

    await screen.findByText("웹 마이크 실시간");
    expect(screen.queryByRole("checkbox", { name: /동의/ })).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("uploads as before with the notice there: consent, then the file", async () => {
    attest.mockResolvedValue(undefined);
    upload.mockResolvedValue(undefined);
    const { container } = render(
      <NewMeetingScreen existingMeetingId="mtg_1" notice={NOTICE} />,
    );
    const file = new File(["x"], "meeting.mp3", { type: "audio/mpeg" });
    const send = screen.getByRole("button", { name: "업로드하고 분석 시작" });

    expect((send as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(container.querySelector("input[type=file]")!, {
      target: { files: [file] },
    });
    fireEvent.click(consent());
    fireEvent.click(send);

    await waitFor(() => expect(push).toHaveBeenCalledWith("/meetings/mtg_1"));
    expect(attest).toHaveBeenCalledWith("mtg_1");
    expect(upload).toHaveBeenCalledWith("mtg_1", file);
  });
});
