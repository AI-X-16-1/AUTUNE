import { describe, expect, it } from "vitest";

import { driveOpenUrl, drivePreviewUrl, parseDriveLink } from "./driveLink";

// What a person pastes becomes a file id and nothing else: the addresses the
// preview uses are built from the id, never from the pasted text.

const ID = "1AbC_dEf-GhIjKlMnOpQrStUvWxYz012345";

describe("parseDriveLink", () => {
  it.each([
    [`https://drive.google.com/file/d/${ID}/view?usp=sharing`, "file"],
    [`https://drive.google.com/file/d/${ID}/preview`, "file"],
    [`https://drive.google.com/open?id=${ID}`, "file"],
    [`https://drive.google.com/uc?id=${ID}&export=download`, "file"],
    [`https://docs.google.com/document/d/${ID}/edit#heading=h.abc`, "document"],
    [
      `https://docs.google.com/presentation/d/${ID}/edit?slide=id.p3`,
      "presentation",
    ],
    [`https://docs.google.com/spreadsheets/d/${ID}/edit#gid=0`, "spreadsheets"],
    [`  ${ID}  `, "file"],
  ])("reads the file out of %s", (link, kind) => {
    expect(parseDriveLink(link)).toEqual({ id: ID, kind });
  });

  it.each([
    ["nothing", ""],
    ["a word", "회의자료"],
    ["another host", `https://example.com/file/d/${ID}/view`],
    [
      "a host that only ends like Drive's",
      `https://drive.google.com.evil.example/file/d/${ID}/view`,
    ],
    [
      "a host that only starts like Drive's",
      `https://evil.example/drive.google.com/file/d/${ID}`,
    ],
    ["plain http", `http://drive.google.com/file/d/${ID}/view`],
    ["a script address", `javascript:alert('${ID}')`],
    ["a data address", `data:text/html,${ID}`],
    ["a folder", `https://drive.google.com/drive/folders/${ID}`],
    ["a Google form", `https://docs.google.com/forms/d/${ID}/edit`],
    ["an id with a slash in it", `https://drive.google.com/open?id=${ID}/../x`],
    [
      "an id that would break out of an address",
      `https://drive.google.com/open?id=${ID}"><script>`,
    ],
    ["a missing id", "https://drive.google.com/file/d/"],
    ["a short id", "https://drive.google.com/file/d/abc/view"],
  ])("takes %s for no file", (_what, link) => {
    expect(parseDriveLink(link)).toBeNull();
  });
});

describe("the addresses built from a file", () => {
  it("previews a plain file through Drive and a Google document through its editor", () => {
    expect(drivePreviewUrl({ id: ID, kind: "file" })).toBe(
      `https://drive.google.com/file/d/${ID}/preview`,
    );
    expect(drivePreviewUrl({ id: ID, kind: "presentation" })).toBe(
      `https://docs.google.com/presentation/d/${ID}/preview`,
    );
  });

  it("opens the file where it lives", () => {
    expect(driveOpenUrl({ id: ID, kind: "file" })).toBe(
      `https://drive.google.com/file/d/${ID}/view`,
    );
    expect(driveOpenUrl({ id: ID, kind: "document" })).toBe(
      `https://docs.google.com/document/d/${ID}/edit`,
    );
  });

  it("carries nothing of the pasted link but the id", () => {
    const pasted = `https://drive.google.com/file/d/${ID}/view?usp=sharing&resourcekey=secret#frag`;
    const file = parseDriveLink(pasted);

    expect(file).not.toBeNull();
    const built = drivePreviewUrl(file!) + driveOpenUrl(file!);
    expect(built).not.toContain("secret");
    expect(built).not.toContain("usp=");
    expect(built).not.toContain("#");
  });
});
