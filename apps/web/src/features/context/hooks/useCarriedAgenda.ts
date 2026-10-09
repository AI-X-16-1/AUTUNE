"use client";

import { useEffect, useState } from "react";

import type { BriefRead } from "../types";

/** One entry of the draft that another module supplies: a title, and one line under it. */
export type CarriedLine = { title: string; detail?: string };

/**
 * Something another module has for the agenda draft: its name, shown over its
 * entries, and its lines for the meeting the brief names as the earlier one.
 * The route supplies these; this feature names the shape and imports none.
 */
export type CarriedSource = {
  label: string;
  lines: (earlierMeetingId: string) => Promise<readonly CarriedLine[]>;
};

/** What one source answered, under its name. Only sources with a line are kept. */
export type CarriedGroup = { label: string; lines: readonly CarriedLine[] };

/**
 * What the route's sources have for this brief's agenda draft (#1147).
 *
 * Asked once per brief, about the earlier meeting the brief already chose
 * (`recap.meeting_id`): the draft does not pick a second "previous meeting" of
 * its own. A brief with no recap asks nothing. A source that cannot be read
 * has nothing — the draft is an addition to the brief and never its error.
 *
 * Answers are kept under the meeting they were asked about, so one that
 * arrives after the brief changed is not shown under the new brief.
 */
export function useCarriedAgenda(
  brief: BriefRead | null,
  sources: readonly CarriedSource[] | undefined,
): readonly CarriedGroup[] {
  const earlier = brief?.recap?.meeting_id ?? null;
  const [read, setRead] = useState<{
    earlier: string | null;
    answers: Record<number, readonly CarriedLine[]>;
  }>({ earlier, answers: {} });

  useEffect(() => {
    if (earlier === null || sources === undefined) return;
    let current = true;
    setRead({ earlier, answers: {} });
    sources.forEach((source, index) => {
      source
        .lines(earlier)
        .then((lines) => {
          if (!current) return;
          setRead((previous) =>
            previous.earlier === earlier
              ? { earlier, answers: { ...previous.answers, [index]: lines } }
              : previous,
          );
        })
        .catch(() => undefined);
    });
    return () => {
      current = false;
    };
  }, [earlier, sources]);

  if (sources === undefined || earlier === null) return [];
  return sources
    .map((source, index) => ({ label: source.label, lines: read.answers[index] ?? [] }))
    .filter((group) => group.lines.length > 0);
}
