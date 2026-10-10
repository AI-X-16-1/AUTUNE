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
 * What the sources have so far, and whether any of them is still out. An
 * empty `groups` means "nothing to list" only once `waiting` is false.
 */
export type CarriedAgenda = { groups: readonly CarriedGroup[]; waiting: boolean };

const NOTHING_ASKED: CarriedAgenda = { groups: [], waiting: false };

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
 *
 * `waiting` is true from the first render of a brief until every source has
 * answered about its earlier meeting, so the panel does not say there is
 * nothing to draft from while it is still finding out (review of #1193). A
 * source that cannot be read has answered: with nothing.
 */
export function useCarriedAgenda(
  brief: BriefRead | null,
  sources: readonly CarriedSource[] | undefined,
): CarriedAgenda {
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
      const settle = (lines: readonly CarriedLine[]) => {
        if (!current) return;
        setRead((previous) =>
          previous.earlier === earlier
            ? { earlier, answers: { ...previous.answers, [index]: lines } }
            : previous,
        );
      };
      source
        .lines(earlier)
        .then(settle)
        .catch(() => settle([]));
    });
    return () => {
      current = false;
    };
  }, [earlier, sources]);

  if (sources === undefined || earlier === null) return NOTHING_ASKED;
  // The render in which the brief changes comes before the effect above empties
  // what was held, so what was held is read only under the meeting it is about.
  const answers = read.earlier === earlier ? read.answers : {};
  return {
    groups: sources
      .map((source, index) => ({ label: source.label, lines: answers[index] ?? [] }))
      .filter((group) => group.lines.length > 0),
    waiting: Object.keys(answers).length < sources.length,
  };
}
