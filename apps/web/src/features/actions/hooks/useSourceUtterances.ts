"use client";

import { useEffect, useState } from "react";

import { getActionItem } from "../api";
import type {
  ActionItemRead,
  EditHistoryEntry,
  SourceUtterance,
} from "../types";

/**
 * The quotation and the history for one item, fetched when its drawer opens —
 * one request, since `GET /action-items/{id}` returns both.
 *
 * Only `sources` and `history` are taken from the response. Everything else the
 * drawer shows comes from the item the board already holds, which is also what
 * an edit updates — reading the status from here too would show the old one
 * after the user changed it.
 *
 * An item with no source utterances — one somebody typed — has no quotation,
 * and the drawer says why; it is still fetched, because it has a history.
 * `revision` changes when the item is edited, so the history follows the edit.
 */
export function useSourceUtterances(
  item: Pick<ActionItemRead, "id" | "source_utterance_ids">,
  revision = "",
) {
  const expected = item.source_utterance_ids?.length ?? 0;
  const [state, setState] = useState<{
    id: string;
    sources: SourceUtterance[] | null;
    history: EditHistoryEntry[] | null;
    error: Error | null;
  }>({ id: item.id, sources: null, history: null, error: null });

  useEffect(() => {
    // A quick click from one card to the next must not paint the first card's
    // quotation into the second card's drawer, so a response only lands if it
    // is still the item on screen.
    let current = true;
    getActionItem(item.id).then(
      (detail) => {
        if (current)
          setState({
            id: item.id,
            sources: detail.sources,
            history: detail.history ?? [],
            error: null,
          });
      },
      (cause: unknown) => {
        if (current) {
          setState({
            id: item.id,
            sources: null,
            history: null,
            error: cause instanceof Error ? cause : new Error(String(cause)),
          });
        }
      },
    );
    return () => {
      current = false;
    };
  }, [item.id, expected, revision]);

  // State left over from the previous item reads as loading, not as its answer.
  const mine = state.id === item.id;
  const history = mine ? state.history : null;
  if (expected === 0)
    return { sources: [], loading: false, error: null, history };
  if (!mine)
    return { sources: null, loading: true, error: null, history: null };
  return {
    sources: state.sources,
    loading: state.sources === null && !state.error,
    error: state.error,
    history,
  };
}
