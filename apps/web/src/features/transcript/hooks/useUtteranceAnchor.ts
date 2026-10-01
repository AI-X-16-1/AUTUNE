"use client";

import { useEffect, useRef, useState } from "react";

/** How long a row that a link landed on stays tinted. */
const HIGHLIGHT_MS = 2500;

/**
 * Land on the utterance a URL names: `/meetings/{id}#{utterance_id}` (#597).
 *
 * S20 quotes the lines a gap rests on and links each one's time code here, and
 * the utterance id is the anchor because every module already passes it around
 * (`evidence` in agent tools, `utterance_ids` on a topic), so another screen
 * can link the same way without a new contract.
 *
 * **Why the browser cannot do it alone.** Its own hash scroll runs when the
 * page loads, before the transcript has been fetched, so the row it looks for
 * does not exist yet. This runs once the rows are rendered, and again on
 * `hashchange` for a second link followed on the same page.
 *
 * **Once per hash.** The transcript is read again after an S30 report; landing
 * again then would yank the reader back to the linked line from wherever they
 * were reading. A hash that names no line of this meeting is ignored, and the
 * page opens at the top as it did before.
 *
 * Returns the id to tint, or `null`; the tint clears after `HIGHLIGHT_MS`.
 */
export function useUtteranceAnchor(ids: readonly string[] | null): string | null {
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const landed = useRef<string | null>(null);

  useEffect(() => {
    if (!ids) return;
    let timer: number | undefined;

    const land = () => {
      const wanted = hashTarget();
      if (!wanted || wanted === landed.current || !ids.includes(wanted)) return;
      const row = document.getElementById(wanted);
      if (!row) return;
      landed.current = wanted;
      const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      row.scrollIntoView({ block: "center", behavior: still ? "auto" : "smooth" });
      setHighlighted(wanted);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setHighlighted(null), HIGHLIGHT_MS);
    };

    land();
    window.addEventListener("hashchange", land);
    return () => {
      window.removeEventListener("hashchange", land);
      window.clearTimeout(timer);
    };
  }, [ids]);

  return highlighted;
}

function hashTarget(): string | null {
  const raw = window.location.hash.slice(1);
  if (!raw) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    // A malformed escape is not an utterance id.
    return null;
  }
}
