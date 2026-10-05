import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import type { MeetingDetail } from "../types";

const cancelTranscription = vi.fn();
const restartTranscription = vi.fn();
vi.mock("../api", () => ({
  cancelTranscription: (id: string) => cancelTranscription(id),
  restartTranscription: (id: string) => restartTranscription(id),
}));

import { ProcessingStages } from "./ProcessingStages";
import { CancelTranscription, RestartNotice } from "./TranscriptionControls";

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

/** Both parts, as ProcessingStages mounts them. */
function TranscriptionControls({ meeting }: { meeting: MeetingDetail }) {
  return (
    <>
      <CancelTranscription meeting={meeting} />
      <RestartNotice meeting={meeting} />
    </>
  );
}

describe("TranscriptionControls", () => {
  it("draws nothing when there is nothing to do", () => {
    const { container } = render(
      <TranscriptionControls meeting={meeting({})} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("asks before cancelling, then cancels", async () => {
    cancelTranscription.mockResolvedValue({
      meeting_id: "mtg_1",
      status: "failed",
    });
    render(<TranscriptionControls meeting={meeting({ cancellable: true })} />);

    fireEvent.click(screen.getByRole("button", { name: "처리 중단" }));
    expect(cancelTranscription).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "중단하기" }));

    await waitFor(() =>
      expect(cancelTranscription).toHaveBeenCalledWith("mtg_1"),
    );
  });

  it("lets the person back out of the confirmation", () => {
    render(<TranscriptionControls meeting={meeting({ cancellable: true })} />);

    fireEvent.click(screen.getByRole("button", { name: "처리 중단" }));
    fireEvent.click(screen.getByRole("button", { name: "계속 진행" }));

    expect(screen.queryByRole("button", { name: "중단하기" })).toBeNull();
    expect(cancelTranscription).not.toHaveBeenCalled();
  });

  it("offers a restart for a stalled run whose upload is still there", async () => {
    restartTranscription.mockResolvedValue({
      meeting_id: "mtg_1",
      status: "analyzing",
    });
    render(
      <TranscriptionControls
        meeting={meeting({
          stalled: true,
          restartable: true,
          cancellable: true,
        })}
      />,
    );

    expect(screen.getByRole("status").textContent).toContain("응답이 없");
    fireEvent.click(screen.getByRole("button", { name: "다시 시작" }));

    await waitFor(() =>
      expect(restartTranscription).toHaveBeenCalledWith("mtg_1"),
    );
  });

  it("says why a stalled run cannot restart, and offers only cancel", () => {
    render(
      <TranscriptionControls
        meeting={meeting({
          stalled: true,
          restartable: false,
          cancellable: true,
        })}
      />,
    );

    expect(screen.queryByRole("button", { name: "다시 시작" })).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("다시 올려");
    expect(screen.getByRole("button", { name: "처리 중단" })).toBeTruthy();
  });

  it("shows Korean copy for the server's refusal, never its English message", async () => {
    cancelTranscription.mockRejectedValue(
      new ApiError(
        409,
        "nothing_to_cancel",
        "meeting mtg_1 has no transcription in progress",
      ),
    );
    render(<TranscriptionControls meeting={meeting({ cancellable: true })} />);

    fireEvent.click(screen.getByRole("button", { name: "처리 중단" }));
    fireEvent.click(screen.getByRole("button", { name: "중단하기" }));

    const text = (await screen.findByRole("alert")).textContent ?? "";
    expect(text).toContain("이미 끝났거나 중단된 처리입니다.");
    expect(text).not.toContain("mtg_1");
  });

  it("falls back to a generic sentence for an unknown code", async () => {
    cancelTranscription.mockRejectedValue(new ApiError(500, "weird", "boom"));
    render(<TranscriptionControls meeting={meeting({ cancellable: true })} />);

    fireEvent.click(screen.getByRole("button", { name: "처리 중단" }));
    fireEvent.click(screen.getByRole("button", { name: "중단하기" }));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "요청을 처리하지 못했습니다.",
    );
  });

  it("re-enables the buttons once the server's view changes", async () => {
    restartTranscription.mockResolvedValue({
      meeting_id: "mtg_1",
      status: "analyzing",
    });
    const { rerender } = render(
      <TranscriptionControls
        meeting={meeting({
          stalled: true,
          restartable: true,
          cancellable: true,
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "다시 시작" }));
    await waitFor(() => expect(restartTranscription).toHaveBeenCalled());

    rerender(
      <TranscriptionControls
        meeting={meeting({
          stalled: false,
          restartable: false,
          cancellable: true,
        })}
      />,
    );

    const button = screen.getByRole("button", {
      name: "처리 중단",
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
  });

  it("clears an earlier refusal when the server's view changes", async () => {
    cancelTranscription.mockRejectedValue(
      new ApiError(409, "nothing_to_cancel", "x"),
    );
    const { rerender } = render(
      <TranscriptionControls meeting={meeting({ cancellable: true })} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "처리 중단" }));
    fireEvent.click(screen.getByRole("button", { name: "중단하기" }));
    await screen.findByRole("alert");

    rerender(
      <TranscriptionControls
        meeting={meeting({ stalled: true, cancellable: true })}
      />,
    );

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("names a long wait in the queue, not a silent worker, when nothing has started", () => {
    render(
      <TranscriptionControls
        meeting={meeting({
          stalled: true,
          restartable: true,
          cancellable: true,
          stage: null,
        })}
      />,
    );

    const notice = screen.getByRole("status").textContent ?? "";
    expect(notice).toContain("15분");
    expect(notice).not.toContain("응답이 없");
  });
});

describe("ProcessingStages", () => {
  it("puts 처리 중단 in the header, beside the overall progress", () => {
    const { container } = render(
      <ProcessingStages
        meeting={meeting({ cancellable: true, stage: "transcribing" })}
      />,
    );

    const header = container.querySelector("header");
    expect(header).not.toBeNull();
    expect(
      within(header as HTMLElement).getByRole("button", { name: "처리 중단" }),
    ).toBeTruthy();
    expect(
      within(header as HTMLElement).getByLabelText("전체 진행률"),
    ).toBeTruthy();
  });

  it("says a cancelled meeting's original is about to go, not already gone", () => {
    render(
      <ProcessingStages
        meeting={meeting({ status: "failed", cancelled: true, stage: null })}
      />,
    );

    const text = screen.getByRole("status").textContent ?? "";
    expect(text).toContain("곧 삭제됩니다");
    expect(text).not.toContain("삭제되었습니다");
  });
});
