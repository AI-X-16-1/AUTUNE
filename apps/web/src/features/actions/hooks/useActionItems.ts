"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  createActionItem,
  deleteActionItem,
  listActionItems,
  updateActionItem,
  type ActionItemDraft,
  type ActionItemFilter,
} from "../api";
import type { ActionItemRead, ActionStatus } from "../types";

/** What the last settled request for one filter left behind. */
interface State {
  key: string;
  items: ActionItemRead[];
  error: Error | null;
  loading: boolean;
  /** Whether a request for this filter has finished at least once. */
  settled: boolean;
}

/**
 * The board's items, and the four things a person can do to them.
 *
 * ADR 0006 makes the extraction a draft the user finishes, so editing is not a
 * secondary path bolted onto a read view — add, edit and delete are why the
 * screen exists, and they live here beside the fetch rather than in three
 * separate call sites.
 *
 * **Every piece of state carries the filter it belongs to**, the rule
 * `useGapReport` follows for the same reason. App Router keeps this component
 * mounted when only the meeting id in the URL changes, so without it the board
 * drew the previous meeting's items under the new meeting's heading, and of two
 * requests in flight the slower, older one won. Raised in review of #292.
 */
export function useActionItems(filter: ActionItemFilter = {}) {
  const key = JSON.stringify(filter);
  const [state, setState] = useState<State>({
    key,
    items: [],
    error: null,
    loading: true,
    settled: false,
  });
  // A response lands only if no later request has started, for this filter or
  // another.
  const latest = useRef(0);

  const reload = useCallback(async () => {
    const ticket = latest.current + 1;
    latest.current = ticket;
    setState((previous) =>
      previous.key === key
        ? { ...previous, error: null, loading: true }
        : { key, items: [], error: null, loading: true, settled: false },
    );
    try {
      const items = await listActionItems(JSON.parse(key) as ActionItemFilter);
      // Replaced through the previous state, not over it: an add, edit or delete
      // that landed while this read was in flight would otherwise be undone by a
      // list fetched before it. Raised in review of #292.
      if (ticket === latest.current) {
        setState((previous) =>
          previous.key === key
            ? { ...previous, items, error: null, loading: false, settled: true }
            : previous,
        );
      }
    } catch (cause) {
      const error = cause instanceof Error ? cause : new Error(String(cause));
      if (ticket === latest.current) {
        // A refresh that failed keeps this filter's items on screen, and only
        // ever this filter's.
        setState((previous) => ({
          key,
          items: previous.key === key ? previous.items : [],
          error,
          loading: false,
          settled: true,
        }));
      }
    }
  }, [key]);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** Apply a change to the list, but only while it is still this filter's list. */
  const update = useCallback(
    (change: (items: ActionItemRead[]) => ActionItemRead[]) =>
      setState((previous) =>
        previous.key === key ? { ...previous, items: change(previous.items) } : previous,
      ),
    [key],
  );

  const add = useCallback(
    async (draft: ActionItemDraft) => {
      const created = await createActionItem(draft);
      update((items) => [...items, created]);
      return created;
    },
    [update],
  );

  const edit = useCallback(
    async (id: string, changes: Partial<ActionItemDraft & { status: ActionStatus }>) => {
      const updated = await updateActionItem(id, changes);
      update((items) => items.map((item) => (item.id === id ? updated : item)));
      return updated;
    },
    [update],
  );

  /**
   * Remove an item the model got wrong.
   *
   * The row is gone on the server — no soft delete, no tombstone
   * (`privacy.md`) — so this drops it locally too. Do not keep the object
   * around to offer an undo: there is nothing on the server left to restore,
   * and an undo that silently re-creates the text would be a second copy of
   * deleted meeting content living in a browser tab.
   */
  const remove = useCallback(
    async (id: string) => {
      await deleteActionItem(id);
      update((items) => items.filter((item) => item.id !== id));
    },
    [update],
  );

  // The reset in `reload` runs in an effect, so the render that first sees a
  // new filter still holds the previous one's state. It reads as loading.
  if (state.key !== key) {
    return { items: [], loading: true, settled: false, error: null, reload, add, edit, remove };
  }
  return { ...state, reload, add, edit, remove };
}
