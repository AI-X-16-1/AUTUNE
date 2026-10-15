"use client";

import Script from "next/script";
import { useEffect, useState } from "react";

/**
 * The Meta Pixel, for the two screens a person sees before they are anybody:
 * `/login` and `/legal` (the user, 2026-10-15). Nothing behind a session mounts
 * it, and neither does `/invite`, whose address carries an invitation token.
 *
 * **Off unless `NEXT_PUBLIC_META_PIXEL_ID` is set**, and set at build time --
 * Next inlines `NEXT_PUBLIC_*`, so the id is an image build argument
 * (`infra/docker/web.Dockerfile`). Leaving it empty renders nothing, which is
 * what every deployment does until the privacy policy names Meta (see
 * `docs/architecture/privacy.md` section 6: what leaves our infrastructure is
 * named where the person reads it).
 *
 * **Two things are checked before anything is drawn.** The id has to be digits
 * only, because it is written into an inline script. And the address must carry
 * no query: the pixel sends the page's full address, and `/login?next=` holds
 * the path somebody was on inside the app.
 *
 * The check on the address runs after mount, so the server's HTML never holds
 * the script and a visitor with a query is not served it at all.
 */
const PIXEL_ID = process.env.NEXT_PUBLIC_META_PIXEL_ID ?? "";
const DIGITS = /^\d{5,20}$/;

export function MetaPixel({ pixelId = PIXEL_ID }: { pixelId?: string }) {
  const [plainAddress, setPlainAddress] = useState(false);

  useEffect(() => {
    setPlainAddress(window.location.search === "");
  }, []);

  if (!DIGITS.test(pixelId) || !plainAddress) return null;

  return (
    <Script id="meta-pixel" strategy="afterInteractive">
      {`!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');fbq('init','${pixelId}');fbq('track','PageView');`}
    </Script>
  );
}
