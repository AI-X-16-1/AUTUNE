import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TopicLinkRow } from "./TopicLinkRow";
import type { TopicLinkRead } from "../types";

// A topic link's title is the sentence of the meeting that says the topic, not
// the noun phrase it is labelled with; the label stands in when no sentence
// could be read.

afterEach(cleanup);

const link = (overrides: Partial<TopicLinkRead> = {}): TopicLinkRead => ({
  id: 1,
  meeting_id: "mtg_now",
  topic_label: "결제 모듈 연동",
  topic_sentence: "결제 모듈 연동은 수요일까지 끝내야 해요.",
  linked_meeting_id: "mtg_old",
  linked_meeting_date: "2026-10-05",
  similarity: 0.8,
  rerank_score: 0.82,
  confidence: 0.8,
  status: "asserted",
  ...overrides,
});

describe("TopicLinkRow", () => {
  it("shows the sentence that says the topic, with the date and score under it", () => {
    render(<TopicLinkRow link={link()} />);

    expect(screen.getByText("결제 모듈 연동은 수요일까지 끝내야 해요.")).toBeTruthy();
    expect(screen.queryByText("결제 모듈 연동")).toBeNull();
    expect(screen.getByText("2026-10-05 · 재순위 0.82")).toBeTruthy();
  });

  it("shows the label when there is no sentence", () => {
    render(<TopicLinkRow link={link({ topic_sentence: null })} />);

    expect(screen.getByText("결제 모듈 연동")).toBeTruthy();
  });

  it("draws a masked span in the sentence as masked", () => {
    render(<TopicLinkRow link={link({ topic_sentence: "결제 모듈 연동은 k***@example.com 담당이에요." })} />);

    expect(screen.getByText("k***@example.com")).toBeTruthy();
  });
});
