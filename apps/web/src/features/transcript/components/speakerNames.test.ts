import { describe, expect, it } from "vitest";

import { speakerNames } from "./StoredTranscript";

const members = [{ user_id: "usr_1", name: "김민경" }];

describe("speakerNames", () => {
  it("shows a name typed for this meeting when no person is attached", () => {
    const nameOf = speakerNames(
      [{ speaker_label: "화자 2", user_id: null, candidate: null, display_name: "외부 디자이너" }],
      members,
    );

    expect(nameOf("화자 2", null)).toBe("외부 디자이너");
  });

  it("prefers the assigned member over any typed name", () => {
    const nameOf = speakerNames(
      [{ speaker_label: "화자 1", user_id: "usr_1", candidate: null, display_name: null }],
      members,
    );

    expect(nameOf("화자 1", null)).toBe("김민경");
  });

  it("keeps the label when nobody is attached and nothing was typed", () => {
    const nameOf = speakerNames(
      [{ speaker_label: "화자 3", user_id: null, candidate: null }],
      members,
    );

    expect(nameOf("화자 3", null)).toBeNull();
  });
});
