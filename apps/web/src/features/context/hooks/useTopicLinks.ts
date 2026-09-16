"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/shared/api/client";

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

  const decide = useCallback(
    async (linkId: number, status: "confirmed" | "rejected") => {
      try {
        const updated = await confirmLink(linkId, status);
        setPending((current) => current.filter((link) => link.id !== linkId));
        if (status === "confirmed") {
          setAsserted((current) => [...current, updated]);
        }
        return updated;
      } catch (cause) {
        // Our copy of the link is stale, not the request: someone else already
        // decided it (409), or its meeting expired and confirm_topic_link
        // can no longer find it (404). Either way the row belongs to a state
        // that no longer exists — refetch instead of leaving a dead row a
        // retry can never succeed against.
        if (cause instanceof ApiError && (cause.status === 409 || cause.status === 404)) {
          void reload();
        }
        throw cause;
      }
    },
    [reload],
  );

  return { asserted, pending, loading, error, reload, decide };
}
