"use client";

import { useCallback, useEffect, useState } from "react";

import {
  createActionItem,
  deleteActionItem,
  listActionItems,
  updateActionItem,
  type ActionItemDraft,
  type ActionItemFilter,
} from "../api";
import type { ActionItemRead, ActionStatus } from "../types";

/**
 * The board's items, and the four things a person can do to them.
 *
 * ADR 0006 makes the extraction a draft the user finishes, so editing is not a
 * secondary path bolted onto a read view — add, edit and delete are why the
 * screen exists, and they live here beside the fetch rather than in three
 * separate call sites.
 */
export function useActionItems(filter: ActionItemFilter = {}) {
  const [items, setItems] = useState<ActionItemRead[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  const key = JSON.stringify(filter);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listActionItems(JSON.parse(key) as ActionItemFilter));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setLoading(false);
    }
  }, [key]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const add = useCallback(async (draft: ActionItemDraft) => {
    const created = await createActionItem(draft);
    setItems((current) => [...current, created]);
    return created;
  }, []);

  const edit = useCallback(
    async (id: string, changes: Partial<ActionItemDraft & { status: ActionStatus }>) => {
      const updated = await updateActionItem(id, changes);
      setItems((current) => current.map((item) => (item.id === id ? updated : item)));
      return updated;
    },
    [],
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
  const remove = useCallback(async (id: string) => {
    await deleteActionItem(id);
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  return { items, loading, error, reload, add, edit, remove };
}
