"use client";

import { useEffect, useState } from "react";

import { PIXEL_ID, optOutOfPixel, pixelOptedOut } from "@/shared/lib/metaPixel";
import { Button } from "@/shared/ui";

import { BODY } from "./LegalDocumentView";

/**
 * The refusal privacy policy 제12조 promises: one button, for this browser.
 * Drawn only on a build that has a pixel -- elsewhere there is nothing to
 * refuse, and the policy says the dev site loads none.
 */
export function PixelOptOut() {
  // Read after mount: the first render has to match the server's, which has
  // no storage.
  const [refused, setRefused] = useState<boolean | null>(null);
  useEffect(() => setRefused(pixelOptedOut()), []);

  if (PIXEL_ID === "" || refused === null) return null;
  return (
    <div className="flex flex-wrap items-center gap-3 text-ink-body" style={BODY}>
      {refused ? (
        <span>이 브라우저에서 광고 측정(Meta 픽셀)을 거부하였습니다.</span>
      ) : (
        <>
          <span>이 브라우저에서 광고 측정(Meta 픽셀)을 거부할 수 있습니다.</span>
          <Button
            tone="secondary"
            size="compact"
            onClick={() => {
              optOutOfPixel();
              setRefused(true);
            }}
          >
            광고 측정 거부
          </Button>
        </>
      )}
    </div>
  );
}
