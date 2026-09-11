"use client";

import { useEffect, useState } from "react";

import { getActionItem } from "../api";
import type { ActionItemRead, SourceUtterance } from "../types";

/**
 * The quotation for one item, fetched when its drawer opens.
 *
 * Only `sources` is taken from the response. Everything else the drawer shows
 * comes from the item the board already holds, which is also what an edit
 * updates — reading the status from here too would show the old one after the
 * user changed it.
 *
 * An item with no source utterances — one somebody typed — asks for nothing:
 * there is no quotation to fetch, and the drawer says why.
 */
export function useSourceUtterances(item: Pick<ActionItemRead, "id" | "source_utterance_ids">) {
  const expected = item.source_utterance_ids?.length ?? 0;
  const [state, setState] = useState<{
    id: string;
    sources: SourceUtterance[] | null;
    error: Error | null;
  }>({ id: item.id, sources: null, error: null });

  useEffect(() => {
    if (expected === 0) return;
    // A quick click from one card to the next must not paint the first card's
    // quotation into the second card's drawer, so a response only lands if it
    // is still the item on screen.
    let current = true;
    getActionItem(item.id).then(
      (detail) => {
        if (current) setState({ id: item.id, sources: detail.sources, error: null });
      },
      (cause: unknown) => {
        if (current) {
          setState({
            id: item.id,
            sources: null,
            error: cause instanceof Error ? cause : new Error(String(cause)),
          });
        }
      },
    );
    return () => {
      current = false;
    };
  }, [item.id, expected]);

  if (expected === 0) return { sources: [], loading: false, error: null };
  // State left over from the previous item reads as loading, not as its answer.
  if (state.id !== item.id) return { sources: null, loading: true, error: null };
  return { sources: state.sources, loading: state.sources === null && !state.error, error: state.error };
}
