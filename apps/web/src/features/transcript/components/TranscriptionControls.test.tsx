import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MeetingDetail } from "../types";

const cancelTranscription = vi.fn();
const restartTranscription = vi.fn();
vi.mock("../api", () => ({
  cancelTranscription: (id: string) => cancelTranscription(id),
  restartTranscription: (id: string) => restartTranscription(id),
}));

import { TranscriptionControls } from "./TranscriptionControls";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function meeting(flags: Partial<MeetingDetail>): MeetingDetail {
  return {
    meeting_id: "mtg_1",
    title: "회의",
    status: "analyzing",
    original_audio_deleted: false,
    pii_masked: false,
    team_id: "team_1",
    stage: "transcribing",
    stage_progress: 0.4,
    stalled: false,
    restartable: false,
    cancellable: false,
    cancelled: false,
    ...flags,
  };
}

describe("TranscriptionControls", () => {
  it("draws nothing when there is nothing to do", () => {
    const { container } = render(<TranscriptionControls meeting={meeting({})} />);
    expect(container.innerHTML).toBe("");
  });

  it("asks before cancelling, then cancels", async () => {
    cancelTranscription.mockResolvedValue({ meeting_id: "mtg_1", status: "failed" });
    render(<TranscriptionControls meeting={meeting({ cancellable: true })} />);

    fireEvent.click(screen.getByRole("button", { name: "처리 취소" }));
    expect(cancelTranscription).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "취소하기" }));

    await waitFor(() => expect(cancelTranscription).toHaveBeenCalledWith("mtg_1"));
  });

  it("lets the person back out of the confirmation", () => {
    render(<TranscriptionControls meeting={meeting({ cancellable: true })} />);

    fireEvent.click(screen.getByRole("button", { name: "처리 취소" }));
    fireEvent.click(screen.getByRole("button", { name: "계속 진행" }));

    expect(screen.queryByRole("button", { name: "취소하기" })).toBeNull();
    expect(cancelTranscription).not.toHaveBeenCalled();
  });

  it("offers a restart for a stalled run whose upload is still there", async () => {
    restartTranscription.mockResolvedValue({ meeting_id: "mtg_1", status: "analyzing" });
    render(
      <TranscriptionControls
        meeting={meeting({ stalled: true, restartable: true, cancellable: true })}
      />,
    );

    expect(screen.getByRole("status").textContent).toContain("응답이 없");
    fireEvent.click(screen.getByRole("button", { name: "다시 시작" }));

    await waitFor(() => expect(restartTranscription).toHaveBeenCalledWith("mtg_1"));
  });

  it("says why a stalled run cannot restart, and offers only cancel", () => {
    render(
      <TranscriptionControls
        meeting={meeting({ stalled: true, restartable: false, cancellable: true })}
      />,
    );

    expect(screen.queryByRole("button", { name: "다시 시작" })).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("다시 올려");
    expect(screen.getByRole("button", { name: "처리 취소" })).toBeTruthy();
  });

  it("shows the server's refusal instead of failing silently", async () => {
    cancelTranscription.mockRejectedValue(new Error("이미 끝난 처리입니다"));
    render(<TranscriptionControls meeting={meeting({ cancellable: true })} />);

    fireEvent.click(screen.getByRole("button", { name: "처리 취소" }));
    fireEvent.click(screen.getByRole("button", { name: "취소하기" }));

    expect((await screen.findByRole("alert")).textContent).toContain("이미 끝난 처리입니다");
  });
});
