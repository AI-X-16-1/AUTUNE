import { describe, expect, it } from "vitest";

import { shownLabel } from "./speaker";

describe("shownLabel", () => {
  it("reads a diarization label as a numbered speaker, counting from one", () => {
    expect(shownLabel("SPEAKER_00")).toBe("화자 1");
    expect(shownLabel("SPEAKER_11")).toBe("화자 12");
  });

  it("leaves a typed name as it is", () => {
    expect(shownLabel("민구")).toBe("민구");
  });

  it("has nothing to show for an empty label", () => {
    expect(shownLabel(null)).toBeUndefined();
    expect(shownLabel("")).toBeUndefined();
  });
});
