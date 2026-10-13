import { describe, expect, it } from "vitest";

import { isSpeakerLabel, speakerLabelShown } from "./speakerLabel";

describe("speakerLabelShown", () => {
  it("counts a diarization label from one, as the transcript does", () => {
    expect(speakerLabelShown("SPEAKER_00")).toBe("화자 1(이름 미지정)");
    expect(speakerLabelShown("SPEAKER_01")).toBe("화자 2(이름 미지정)");
    expect(speakerLabelShown("speaker_10")).toBe("화자 11(이름 미지정)");
  });

  it("adds only the note to a label that already reads 화자 N", () => {
    expect(speakerLabelShown("화자 3")).toBe("화자 3(이름 미지정)");
  });

  it("leaves a name somebody wrote as it is", () => {
    expect(speakerLabelShown("박지영")).toBe("박지영");
    expect(speakerLabelShown("SPEAKER_01 팀장")).toBe("SPEAKER_01 팀장");
    expect(isSpeakerLabel("박지영")).toBe(false);
    expect(isSpeakerLabel("SPEAKER_01")).toBe(true);
  });
});
