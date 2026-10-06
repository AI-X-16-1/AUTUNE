import { describe, expect, it } from "vitest";

import { unassigned } from "./StoredTranscript";

const members = [
  { user_id: "usr_1", name: "김민경" },
  { user_id: "usr_2", name: "강민구" },
  { user_id: "usr_3", name: "박재경" },
];

describe("unassigned", () => {
  it("lists the members nobody is put to first, and an assigned one last with its label", () => {
    const pick = unassigned(
      [
        { speaker_label: "화자 1", user_id: "usr_1", candidate: null },
        { speaker_label: "화자 2", user_id: null, candidate: null },
        { speaker_label: "화자 3", user_id: null, candidate: null },
      ],
      members,
    );

    expect(pick.members).toEqual([
      { user_id: "usr_2", name: "강민구" },
      { user_id: "usr_3", name: "박재경" },
      { user_id: "usr_1", name: "김민경", assignedTo: "화자 1" },
    ]);
  });

  it("does not suggest a candidate who is already another speaker", () => {
    const pick = unassigned([{ speaker_label: "화자 1", user_id: "usr_1", candidate: null }], members);

    expect(pick.candidate({ user_id: "usr_1", name: "김민경", similarity: 0.81 })).toBeNull();
    expect(pick.candidate({ user_id: "usr_2", name: "강민구", similarity: 0.77 })).toEqual({
      user_id: "usr_2",
      name: "강민구",
      similarity: 0.77,
    });
  });

  it("offers every member as they are while nobody is assigned", () => {
    const pick = unassigned([{ speaker_label: "화자 1", user_id: null, candidate: null }], members);

    expect(pick.members).toEqual(members);
  });
});
