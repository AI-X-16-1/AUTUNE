import { Fragment } from "react";

import { PiiToken } from "@/shared/ui";

/**
 * Transcript text with its masked spans marked.
 *
 * Masking happens in module A before the first write (`privacy.md` section 2),
 * and the format preserves shape while removing content — `010-****-5678`,
 * `k***@example.com`. What arrives here is already masked; this only makes the
 * masking visible, so the reader can tell a redaction from a typo.
 *
 * There is no reveal control and there is nothing to reveal: the unmasked string
 * was never stored. `PiiToken` says so in its tooltip.
 */
const MASKED = /(\S*\*{2,}\S*)/g;

export function MaskedText({ children }: { children: string }) {
  return (
    <>
      {children.split(MASKED).map((part, index) =>
        // The capturing split alternates plain / matched, so odd indices are the
        // masked runs. Keys are positional because the same span can repeat.
        index % 2 === 1 ? (
          <PiiToken key={index}>{part}</PiiToken>
        ) : (
          <Fragment key={index}>{part}</Fragment>
        ),
      )}
    </>
  );
}
