"use client";

import { useEffect, useState } from "react";

import { getActionItem } from "../api";
import { pointsAtNothing } from "../board";
import type { ActionItemRead, SourceUtterance } from "../types";

/**
 * The lines said just before a card's sentence, for the cards whose sentence
 * says nothing by itself -- "그럴게", "다음 주까지 볼게요" (the user, 2026-10-02).
 *
 * Where the model writes summaries the sentence is rewritten to say what it is
 * about, and this fetches nothing. Where it does not (the resolver is off, or
 * it kept the utterance as said), the card showed an answer with no question:
 * the line it answers was one click away, in the detail window.
 *
 * **Asked for per card, when it is on the board.** The list carries utterance
 * ids and never their words (`ActionItemDetail`); a quotation leaves the
 * server only for an item somebody is looking at. So this is the same request
 * the detail window makes, sent for the few cards that need it, and never a
 * wider list response.
 *
 * At most `MAX_CARDS` at a time and two lines each: it is a hint beside a
 * card, not the transcript. A card whose request fails simply shows no hint.
 */

const LINES = 2;
const MAX_CARDS = 12;

export function useCardContext(items: ActionItemRead[]): Record<string, SourceUtterance[]> {
  const [known, setKnown] = useState<Record<string, SourceUtterance[]>>({});

  const wanted = items.filter(pointsAtNothing).slice(0, MAX_CARDS);
  const missing = wanted.filter((item) => !(item.id in known)).map((item) => item.id);
  const key = missing.join(",");

  useEffect(() => {
    if (key === "") return;
    let current = true;
    for (const id of key.split(",")) {
      getActionItem(id).then(
        (detail) => {
          if (current) {
            setKnown((before) => ({ ...before, [id]: (detail.context ?? []).slice(-LINES) }));
          }
        },
        () => {
          // Remembered as "no hint", so a failing card is not asked about again
          // on every render.
          if (current) setKnown((before) => ({ ...before, [id]: [] }));
        },
      );
    }
    return () => {
      current = false;
    };
  }, [key]);

  // Only for cards that still need it: an item edited into a full sentence
  // stops showing the hint it once had.
  const shown: Record<string, SourceUtterance[]> = {};
  for (const item of wanted) {
    const lines = known[item.id];
    if (lines !== undefined && lines.length > 0) shown[item.id] = lines;
  }
  return shown;
}
