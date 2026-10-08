"use client";

import { useState } from "react";

import { MaskedText, Quote } from "@/shared/ui";

import type { SourceUtterance } from "../types";

/**
 * One source utterance, quoted as the evidence of an item or a decision.
 *
 * A promise or a decision made in the middle of a long turn is quoted as the
 * part it was made from (`excerpt`), not the whole turn: the reader checks one
 * sentence against the summary above it. The part is cut from what was said,
 * so the whole turn only adds to it, and it is one press away.
 */
export function SourceQuote({ source }: { source: SourceUtterance }) {
  const [whole, setWhole] = useState(false);
  if (!source.excerpt) {
    return (
      <Quote>
        <MaskedText>{source.text}</MaskedText>
      </Quote>
    );
  }
  return (
    <Quote>
      <MaskedText>{whole ? source.text : source.excerpt}</MaskedText>
      <button
        type="button"
        aria-expanded={whole}
        onClick={() => setWhole((shown) => !shown)}
        className="ml-2 text-[var(--color-ink-muted)] underline underline-offset-2"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        {whole ? "해당 부분만 보기" : "전체 발화 보기"}
      </button>
    </Quote>
  );
}
