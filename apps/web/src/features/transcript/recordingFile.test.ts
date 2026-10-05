import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

const getMeeting = vi.fn();
vi.mock("./api", () => ({ getMeeting: (id: string) => getMeeting(id) }));

import {
  acceptsRecording,
  recordingToSave,
  saveRecordingFile,
  serverHasRecording,
} from "./recordingFile";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  getMeeting.mockReset();
});

describe("saveRecordingFile", () => {
  it("hands the recording to the browser as a download named after the meeting", () => {
    vi.useFakeTimers();
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
  });

  it("lets the URL go only after the download had time to start", () => {
    // Revoked in the same tick, some browsers never start a large download --
    // and here a lost download is a lost meeting.
    vi.useFakeTimers();
    const revoked = vi.fn();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: () => "blob:recording",
      revokeObjectURL: revoked,
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    saveRecordingFile(new Blob(["x"]), "mtg_1");
    expect(revoked).not.toHaveBeenCalled();

    vi.advanceTimersByTime(60_000);
    expect(revoked).toHaveBeenCalledExactlyOnceWith("blob:recording");
  });
});

describe("recordingToSave", () => {
  const recording = new Blob(["x"]);

  it("gives the recording only after an upload failed", () => {
    expect(recordingToSave("upload_failed", recording)).toBe(recording);
  });

  it.each(["uploading", "done", "recording", "idle"] as const)(
    "gives nothing while %s",
    (phase) => {
      expect(recordingToSave(phase, recording)).toBeNull();
    },
  );

  it("gives nothing when the tab holds no recording", () => {
    expect(recordingToSave("upload_failed", null)).toBeNull();
  });
});

describe("serverHasRecording", () => {
  it("takes a 409 as the server already having it", async () => {
    await expect(
      serverHasRecording(
        "mtg_1",
        new ApiError(409, "conflict", "meeting is analyzing"),
      ),
    ).resolves.toBe(true);
    expect(getMeeting).not.toHaveBeenCalled();
  });

  it.each([
    [403, "permission_denied"],
    [413, "payload_too_large"],
    // Accepted, then the queue refused: the server deleted the file and
    // failed the meeting, so the tab's copy is the only one left.
    [500, "enqueue_failed"],
  ])(
    "takes a %i answer from the API as the server not having it",
    async (status, code) => {
      await expect(
        serverHasRecording("mtg_1", new ApiError(status, code, "no")),
      ).resolves.toBe(false);
      expect(getMeeting).not.toHaveBeenCalled();
    },
  );

  it.each(["analyzing", "complete"])(
    "after a lost response, asks the meeting and finds it %s -- the server has it",
    async (status) => {
      getMeeting.mockResolvedValue({ status });
      await expect(
        serverHasRecording("mtg_1", new TypeError("Failed to fetch")),
      ).resolves.toBe(true);
      expect(getMeeting).toHaveBeenCalledWith("mtg_1");
    },
  );

  it("after a gateway timeout, finds the meeting still recording -- the server does not have it", async () => {
    getMeeting.mockResolvedValue({ status: "recording" });
    await expect(
      serverHasRecording(
        "mtg_1",
        new ApiError(504, "unknown", "Gateway Timeout"),
      ),
    ).resolves.toBe(false);
  });

  it("when even the meeting cannot be read, does not claim the server has it", async () => {
    getMeeting.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(
      serverHasRecording("mtg_1", new TypeError("Failed to fetch")),
    ).resolves.toBe(false);
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
