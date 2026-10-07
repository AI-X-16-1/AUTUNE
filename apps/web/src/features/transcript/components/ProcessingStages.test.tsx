import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MeetingDetail } from "../types";

vi.mock("../api", () => ({
  cancelTranscription: vi.fn(),
  restartTranscription: vi.fn(),
}));

import { ProcessingStages } from "./ProcessingStages";

afterEach(cleanup);

function meeting(fields: Partial<MeetingDetail>): MeetingDetail {
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
    ...fields,
  };
}

/** The state label at the end of the row titled `label`. */
function stateOf(label: string): string | null {
  const row = screen.getByText(label).closest("li");
  return row?.lastElementChild?.textContent ?? null;
}

describe("ProcessingStages", () => {
  // The meeting row keeps both flags from the last transcription that
  // finished, and a new upload for the same meeting does not clear them. While
  // the new run is still recognising speech, its original has not been deleted
  // and nothing has been masked, whatever the previous run left behind.
  it("does not show a previous run's deletion and masking during a new run", () => {
    render(
      <ProcessingStages
        meeting={meeting({ original_audio_deleted: true, pii_masked: true })}
      />,
    );

    expect(stateOf("음성 인식")).toBe("진행 40%");
    expect(stateOf("원본 음성 삭제")).toBe("대기");
    expect(stateOf("개인정보 마스킹 · 저장")).toBe("대기");
  });

  it("shows the original deleted once the running step reaches masking", () => {
    render(<ProcessingStages meeting={meeting({ stage: "masking", stage_progress: 0 })} />);

    expect(stateOf("원본 음성 삭제")).toBe("완료");
  });

  // A re-upload that fails or is cancelled leaves the meeting `failed` with
  // no running job, so `stage` is null. The masking flag is still the
  // previous run's; this run's original is deleted by the failure path.
  it("does not show a previous run's masking for a run that failed", () => {
    render(
      <ProcessingStages
        meeting={meeting({
          status: "failed",
          stage: null,
          stage_progress: null,
          original_audio_deleted: true,
          pii_masked: true,
        })}
      />,
    );

    expect(stateOf("원본 음성 삭제")).toBe("완료");
    expect(stateOf("개인정보 마스킹 · 저장")).toBe("대기");
  });

  // #859 review: with the stored-flag test gone, nothing drew a finished
  // meeting. Every pipeline step reads 완료 there.
  it("shows every step done for a finished meeting", () => {
    render(
      <ProcessingStages
        meeting={meeting({ status: "complete", stage: null, stage_progress: null })}
      />,
    );

    for (const label of ["음성 인식", "화자 분리", "원본 음성 삭제", "개인정보 마스킹 · 저장"]) {
      expect(stateOf(label)).toBe("완료");
    }
  });
});
