import { describe, expect, it } from "vitest";

import { unassigned } from "./StoredTranscript";

const members = [
  { user_id: "usr_1", name: "김민경" },
  { user_id: "usr_2", name: "강민구" },
  { user_id: "usr_3", name: "박재경" },
];

describe("unassigned", () => {
  it("leaves a member already put to one speaker out of the others' pickers", () => {
    const pick = unassigned(
      [
        { speaker_label: "화자 1", user_id: "usr_1", candidate: null },
        { speaker_label: "화자 2", user_id: null, candidate: null },
        { speaker_label: "화자 3", user_id: null, candidate: null },
      ],
      members,
    );

    expect(pick.members.map((member) => member.name)).toEqual(["강민구", "박재경"]);
  });

  it("drops a candidate who is already assigned to another speaker", () => {
    const pick = unassigned(
      [
        { speaker_label: "화자 1", user_id: "usr_1", candidate: null },
        {
          speaker_label: "화자 2",
          user_id: null,
          candidate: { user_id: "usr_1", name: "김민경", similarity: 0.81 },
        },
      ],
      members,
    );

    expect(pick.candidate({ user_id: "usr_1", name: "김민경", similarity: 0.81 })).toBeNull();
    expect(pick.candidate({ user_id: "usr_2", name: "강민구", similarity: 0.77 })).toEqual({
      user_id: "usr_2",
      name: "강민구",
      similarity: 0.77,
    });
  });

  it("offers every member while nobody is assigned", () => {
    const pick = unassigned([{ speaker_label: "화자 1", user_id: null, candidate: null }], members);

    expect(pick.members).toEqual(members);
  });
});
