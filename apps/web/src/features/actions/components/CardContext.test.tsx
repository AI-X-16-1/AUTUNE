import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionBoard } from "./ActionBoard";
import { pointsAtNothing } from "../board";
import { MAX_CARDS } from "../hooks/useCardContext";
import type { ActionItemRead, SourceUtterance } from "../types";

// A card whose sentence says nothing by itself -- "그럴게" -- shows the line
// said just before it (the user, 2026-10-02). Asked for per card, the way the
// detail window asks: the list never carries an utterance's words.

const detail = vi.fn<(id: string) => Promise<{ context?: SourceUtterance[] }>>();
vi.mock("../api", () => ({ getActionItem: (id: string) => detail(id) }));

function item(id: string, description: string, over: Partial<ActionItemRead> = {}): ActionItemRead {
  return {
    id,
    meeting_id: "mtg_1",
    description,
    status: "todo",
    confidence: 0.9,
    is_candidate: false,
    origin: "model",
    description_resolved: false,
    source_utterance_ids: ["utt_1"],
    summary: null,
    ...over,
  } as ActionItemRead;
}

const BEFORE: SourceUtterance[] = [
  { id: "utt_a", text: "이번 주 배포 일정은 누가 정리하나요?" },
  { id: "utt_b", text: "민구님이 금요일까지 공유해 주실 수 있을까요?" },
  { id: "utt_c", text: "가능하시면요." },
];

afterEach(() => {
  cleanup();
  detail.mockReset();
});

describe("pointsAtNothing", () => {
  it("is a short sentence the pipeline took from an utterance and nobody rewrote", () => {
    expect(pointsAtNothing(item("a", "그럴게"))).toBe(true);
    expect(pointsAtNothing(item("a", "다음 주 화요일까지 볼게요"))).toBe(true);
  });

  it("is not a sentence long enough to say what it is about", () => {
    expect(pointsAtNothing(item("a", "제가 금요일까지 스펙 초안을 정리해서 공유하겠습니다"))).toBe(false);
  });

  it("is not a sentence a model or a person already wrote", () => {
    expect(pointsAtNothing(item("a", "그럴게", { description_resolved: true }))).toBe(false);
    expect(pointsAtNothing(item("a", "그럴게", { origin: "user" }))).toBe(false);
    expect(pointsAtNothing(item("a", "그럴게", { origin: "chat" }))).toBe(false);
  });

  it("is not an item with no utterance behind it", () => {
    expect(pointsAtNothing(item("a", "그럴게", { source_utterance_ids: [] }))).toBe(false);
  });
});

describe("a card on the board", () => {
  it("shows the two lines said just before a sentence that says nothing by itself", async () => {
    detail.mockResolvedValue({ context: BEFORE });

    render(<ActionBoard items={[item("short", "그럴게")]} />);

    const hint = await screen.findByRole("group", { name: "앞선 발화" });
    expect(within(hint).getByText("민구님이 금요일까지 공유해 주실 수 있을까요?")).toBeTruthy();
    expect(within(hint).getByText("가능하시면요.")).toBeTruthy();
    // The nearest two, not the whole run-up.
    expect(within(hint).queryByText("이번 주 배포 일정은 누가 정리하나요?")).toBeNull();
    expect(detail).toHaveBeenCalledExactlyOnceWith("short");
  });

  it("asks for nothing when every sentence stands by itself", async () => {
    render(
      <ActionBoard
        items={[
          item("long", "제가 금요일까지 스펙 초안을 정리해서 공유하겠습니다"),
          item("ai", "그럴게", { description_resolved: true }),
          item("typed", "그럴게", { origin: "user" }),
        ]}
      />,
    );

    await waitFor(() => expect(screen.getAllByRole("button").length).toBeGreaterThan(0));
    expect(detail).not.toHaveBeenCalled();
    expect(screen.queryByRole("group", { name: "앞선 발화" })).toBeNull();
  });

  it("asks once per card, however often the board draws", async () => {
    detail.mockResolvedValue({ context: BEFORE });
    const items = [item("short", "그럴게")];
    const { rerender } = render(<ActionBoard items={items} />);
    await screen.findByRole("group", { name: "앞선 발화" });

    rerender(<ActionBoard items={[...items]} selectedId="short" />);
    rerender(<ActionBoard items={[...items]} />);

    await screen.findByRole("group", { name: "앞선 발화" });
    expect(detail).toHaveBeenCalledTimes(1);
  });

  it("shows the card without a hint when the lines could not be read, and does not ask about it again", async () => {
    detail.mockRejectedValueOnce(new Error("502"));
    detail.mockResolvedValue({ context: BEFORE });
    const failing = item("short", "그럴게");
    const { rerender } = render(<ActionBoard items={[failing]} />);
    await waitFor(() => expect(detail).toHaveBeenCalledTimes(1));

    // Another card arrives. Only the new one is asked about: a card whose
    // request failed is remembered as having no hint.
    rerender(<ActionBoard items={[failing, item("other", "제가 볼게요")]} />);

    await screen.findByRole("group", { name: "앞선 발화" });
    expect(detail.mock.calls.map(([id]) => id)).toEqual(["short", "other"]);
    expect(screen.getByText("그럴게")).toBeTruthy();
    expect(screen.getAllByRole("group", { name: "앞선 발화" })).toHaveLength(1);
  });

  it("drops the hint once the sentence has been rewritten", async () => {
    detail.mockResolvedValue({ context: BEFORE });
    const { rerender } = render(<ActionBoard items={[item("short", "그럴게")]} />);
    await screen.findByRole("group", { name: "앞선 발화" });

    rerender(
      <ActionBoard items={[item("short", "금요일까지 배포 일정을 정리해 공유한다", { origin: "model" })]} />,
    );

    await waitFor(() => expect(screen.queryByRole("group", { name: "앞선 발화" })).toBeNull());
  });

  it("asks about the first twelve such cards and never a thirteenth", async () => {
    // Each request carries quoted lines, so what a board fires by being opened
    // is bounded -- on the board, not on requests in flight.
    detail.mockResolvedValue({ context: BEFORE });
    const many = Array.from({ length: MAX_CARDS + 2 }, (_, n) => item(`short_${n}`, "그럴게"));

    const { rerender } = render(<ActionBoard items={many} />);

    await waitFor(() =>
      expect(screen.getAllByRole("group", { name: "앞선 발화" })).toHaveLength(MAX_CARDS),
    );
    expect(MAX_CARDS).toBe(12);
    expect(detail.mock.calls.map(([id]) => id)).toEqual(many.slice(0, MAX_CARDS).map((i) => i.id));

    // The first twelve are known now. The rest are still not asked about.
    rerender(<ActionBoard items={[...many]} />);
    await waitFor(() =>
      expect(screen.getAllByRole("group", { name: "앞선 발화" })).toHaveLength(MAX_CARDS),
    );
    expect(detail).toHaveBeenCalledTimes(MAX_CARDS);
  });

  it("shows the nearest two of however many lines came before", async () => {
    detail.mockResolvedValue({
      context: [1, 2, 3, 4, 5].map((n) => ({ id: `utt_${n}`, text: `${n}번째 줄` })),
    });

    render(<ActionBoard items={[item("short", "그럴게")]} />);

    const hint = await screen.findByRole("group", { name: "앞선 발화" });
    expect(within(hint).getAllByText(/번째 줄/).map((line) => line.textContent)).toEqual([
      "4번째 줄",
      "5번째 줄",
    ]);
  });

  it("shows it for a candidate too", async () => {
    detail.mockResolvedValue({ context: BEFORE });

    render(<ActionBoard items={[item("cand", "그럴게", { is_candidate: true })]} />);

    const band = await screen.findByRole("region", { name: "후보" });
    expect(await within(band).findByRole("group", { name: "앞선 발화" })).toBeTruthy();
  });
});
