/**
 * A Google Drive file, from what a person pastes.
 *
 * People paste a share link, an "open" link, the address bar of a Google
 * document, or the bare id. All this takes from any of them is the file's id
 * and, where the link says so, which Google editor it belongs to -- and the
 * addresses the preview uses are then built here, from the id alone.
 *
 * **Nothing a person typed is ever used as an address.** A frame's `src` made
 * from pasted text is a frame that can be pointed anywhere; one made from an
 * id that matched `ID` and a host written in this file can only be Google's
 * preview of that file. That is the whole reason this is a parser and not a
 * `startsWith`.
 */

/** Which preview a file has. A Google document has its own; anything else is a plain Drive file. */
export type DriveKind = "file" | "document" | "presentation" | "spreadsheets";

export type DriveFile = { id: string; kind: DriveKind };

/** Drive ids are URL-safe base64-ish. Long enough not to be a word, short enough to be an id. */
const ID = /^[A-Za-z0-9_-]{10,200}$/;

const EDITORS: readonly DriveKind[] = [
  "document",
  "presentation",
  "spreadsheets",
];

function checked(
  id: string | null | undefined,
  kind: DriveKind,
): DriveFile | null {
  return id !== null && id !== undefined && ID.test(id) ? { id, kind } : null;
}

/**
 * The file a pasted link or id names, or null when it names none.
 *
 * Accepts `https://drive.google.com/file/d/<id>/...`, `.../open?id=<id>`,
 * `.../uc?id=<id>`, `https://docs.google.com/{document,presentation,spreadsheets}/d/<id>/...`
 * and a bare id. Anything else -- another host, another scheme, a Drive
 * folder -- is null.
 */
export function parseDriveLink(input: string): DriveFile | null {
  const text = input.trim();
  if (ID.test(text)) return { id: text, kind: "file" };

  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol !== "https:") return null;

  const parts = url.pathname.split("/").filter((part) => part !== "");
  if (url.hostname === "drive.google.com") {
    if (parts[0] === "file" && parts[1] === "d")
      return checked(parts[2], "file");
    if (parts[0] === "open" || parts[0] === "uc")
      return checked(url.searchParams.get("id"), "file");
    return null;
  }
  if (url.hostname === "docs.google.com") {
    const kind = EDITORS.find((editor) => editor === parts[0]);
    if (kind !== undefined && parts[1] === "d") return checked(parts[2], kind);
  }
  return null;
}

/** Google's own embeddable preview of the file. Built from the id; see the note at the top. */
export function drivePreviewUrl(file: DriveFile): string {
  return file.kind === "file"
    ? `https://drive.google.com/file/d/${file.id}/preview`
    : `https://docs.google.com/${file.kind}/d/${file.id}/preview`;
}

/** Where the file opens in Drive itself, for "open it there" beside a preview that would not load. */
export function driveOpenUrl(file: DriveFile): string {
  return file.kind === "file"
    ? `https://drive.google.com/file/d/${file.id}/view`
    : `https://docs.google.com/${file.kind}/d/${file.id}/edit`;
}
