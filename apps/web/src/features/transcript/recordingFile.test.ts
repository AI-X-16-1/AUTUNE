import { afterEach, describe, expect, it, vi } from "vitest";

import { acceptsRecording, saveRecordingFile } from "./recordingFile";

afterEach(() => vi.restoreAllMocks());

describe("saveRecordingFile", () => {
  it("hands the recording to the browser as a download named after the meeting, then lets the URL go", () => {
    const created = vi.fn(() => "blob:recording");
    const revoked = vi.fn();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: created,
      revokeObjectURL: revoked,
    });
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(`${this.download} ${this.getAttribute("href")}`);
    });
    const recording = new File([new Uint8Array([1, 2, 3])], "live.webm", {
      type: "audio/webm",
    });

    saveRecordingFile(recording, "mtg_1");

    expect(created).toHaveBeenCalledExactlyOnceWith(recording);
    expect(clicked).toEqual(["autune-녹음-mtg_1.webm blob:recording"]);
    expect(revoked).toHaveBeenCalledExactlyOnceWith("blob:recording");
    vi.unstubAllGlobals();
  });
});

describe("acceptsRecording", () => {
  it.each(["회의.mp3", "회의.WAV", "회의.m4a", "autune-녹음-mtg_1.webm"])(
    "takes %s",
    (name) => {
      expect(acceptsRecording(name)).toBe(true);
    },
  );

  it.each(["회의.ogg", "회의", "회의.webm.txt"])("refuses %s", (name) => {
    expect(acceptsRecording(name)).toBe(false);
  });
});
