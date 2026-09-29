"use client";

import { useEffect, useState } from "react";

import { listTemplates } from "../api";
import type { TemplateOption } from "../types";

/**
 * The templates a meeting can be held to, for the rail's picker.
 *
 * Read once per mount. The list is reference data from files in the backend
 * package, the same for every meeting and unchanged at runtime, so it is not
 * polled and not keyed by meeting.
 *
 * A failure leaves the list empty rather than surfacing an error: the rail
 * still shows the template in force, and a picker with nothing to pick is
 * simply not drawn. Choosing another checklist is an extra, not the report.
 */
export function useTemplates(): TemplateOption[] {
  const [templates, setTemplates] = useState<TemplateOption[]>([]);

  useEffect(() => {
    let live = true;
    listTemplates()
      .then((loaded) => {
        if (live) setTemplates(loaded);
      })
      .catch(() => {
        // See above: no picker is the whole fallback.
      });
    return () => {
      live = false;
    };
  }, []);

  return templates;
}
