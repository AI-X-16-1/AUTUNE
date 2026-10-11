"use client";

import { useEffect, useRef } from "react";

import { TITLE_READS_MS } from "../titleReads";

/**
 * Asks for the titles after a run (`titleReads`): `read` is called at each of
 * `TITLE_READS_MS` after `run` moves, while `waiting` says a row has none.
 *
 * `run` counts the runs seen to end on this screen. At 0 none has, and nothing
 * is asked: a page that was only opened holds what the server had. Each moment
 * looks at `waiting` as it is then, not as it was when the run ended -- the
 * rows the run wrote may not have been read yet at that point -- so a moment
 * that finds every row titled asks nothing, and the reads stop there.
 */
export function useTitleReads(run: number, waiting: boolean, read: () => void): void {
  // Both are new on every render; a ref keeps them out of the effect's
  // dependencies, so only a run starts the clock.
  const now = useRef({ waiting, read });
  now.current = { waiting, read };

  useEffect(() => {
    if (run === 0) return;
    const timers = TITLE_READS_MS.map((ms) =>
      window.setTimeout(() => {
        if (now.current.waiting) now.current.read();
      }, ms),
    );
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [run]);
}
