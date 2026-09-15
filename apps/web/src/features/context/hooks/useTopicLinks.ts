"use client";

import { useCallback, useEffect, useState } from "react";

import { confirmLink, getLinks } from "../api";
import type { TopicLinkRead } from "../types";

/**
 * One meeting's topic links, split asserted/pending, plus the one thing a
 * person can do to a pending link: confirm or reject it (S15/S22).
 *
 * A rejected link is not soft-deleted client-side either — it just leaves both
 * arrays, the same "gone, not flagged" rule module B's action items follow.
 */
export function useTopicLinks(meetingId: string) {
  const [asserted, setAsserted] = useState<TopicLinkRead[]>([]);
  const [pending, setPending] = useState<TopicLinkRead[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const links = await getLinks(meetingId);
      setAsserted(links.asserted);
      setPending(links.pending);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setLoading(false);
    }
  }, [meetingId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const decide = useCallback(async (linkId: number, status: "confirmed" | "rejected") => {
    const updated = await confirmLink(linkId, status);
    setPending((current) => current.filter((link) => link.id !== linkId));
    if (status === "confirmed") {
      setAsserted((current) => [...current, updated]);
    }
    return updated;
  }, []);

  return { asserted, pending, loading, error, reload, decide };
}
