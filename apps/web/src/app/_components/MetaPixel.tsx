"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { loadPixel, pixelAllowed, trackPageView } from "@/shared/lib/metaPixel";

/**
 * Loads the Meta Pixel on the screens before sign-in and counts a page view
 * on each of them (`shared/lib/metaPixel`). Mounted once in the root layout;
 * renders nothing.
 *
 * Inside the app the script, once loaded, stays on the page -- a client-side
 * navigation does not unload it -- but nothing is sent from there: the pixel's
 * own navigation counting is off, and this component sends only on a pixel
 * path.
 */
export function MetaPixel() {
  const pathname = usePathname();
  useEffect(() => {
    if (!pixelAllowed(pathname)) return;
    loadPixel();
    trackPageView();
  }, [pathname]);
  return null;
}
