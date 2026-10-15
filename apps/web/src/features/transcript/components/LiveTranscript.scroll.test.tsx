import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { LiveRow } from "../types";
import { LiveTranscript } from "./LiveTranscript";

// The transcript scrolls apart from the rail and follows the newest row while
// the reader is at the bottom (the user, 2026-10-14).

afterEach(cleanup);

const row = (i: number) =>
  ({
    utterance: {
      id: `utt_live_${i}`,
      speaker: "화자 1",
      speaker_id: null,
      role: null,
      start: i,
      end: i + 1,
      text: `말 ${i}`,
      confidence: 1,
    },
  }) as LiveRow;

const rowsUpTo = (n: number) => Array.from({ length: n }, (_, i) => row(i));

function draw(rows: LiveRow[]) {
  return (
    <LiveTranscript state="recording" rows={rows} elapsedSeconds={0} levels={[]} />
  );
}

/** jsdom lays nothing out: give the transcript a height and a content height. */
function sized(element: HTMLElement, content: { height: number }) {
  Object.defineProperty(element, "clientHeight", { configurable: true, value: 400 });
  Object.defineProperty(element, "scrollHeight", {
    configurable: true,
    get: () => content.height,
  });
}

describe("LiveTranscript scrolling", () => {
  it("follows a new row to the bottom", () => {
    const content = { height: 400 };
    const { rerender } = render(draw(rowsUpTo(1)));
    const transcript = screen.getByRole("main");
    sized(transcript, content);

    content.height = 1000;
    rerender(draw(rowsUpTo(2)));

    expect(transcript.scrollTop).toBe(1000);
  });

  it("leaves a reader who scrolled up where they are, and follows again at the bottom", () => {
    const content = { height: 1000 };
    const { rerender } = render(draw(rowsUpTo(2)));
    const transcript = screen.getByRole("main");
    sized(transcript, content);

    transcript.scrollTop = 100;
    fireEvent.scroll(transcript);
    content.height = 1200;
    rerender(draw(rowsUpTo(3)));
    expect(transcript.scrollTop).toBe(100);

    transcript.scrollTop = 800;
    fireEvent.scroll(transcript);
    content.height = 1400;
    rerender(draw(rowsUpTo(4)));
    expect(transcript.scrollTop).toBe(1400);
  });
});
