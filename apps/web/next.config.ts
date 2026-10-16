import type { NextConfig } from "next";

import { contentSecurityPolicy } from "./src/shared/security/contentSecurityPolicy";

/**
 * `/api/*` is proxied to the FastAPI app so the browser sees one origin. That
 * keeps the `autune_session` cookie first-party (a cross-site `SameSite=Lax`
 * cookie would not ride along on `fetch`) and avoids CORS entirely — including
 * the Google OAuth round trip, whose callback must land back on this origin.
 *
 * Set `API_PROXY_TARGET` per environment (defaults to the local API port).
 */
const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

const config: NextConfig = {
  reactStrictMode: true,
  typedRoutes: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_PROXY_TARGET}/api/:path*` }];
  },
  // Every response carries the app's Content-Security-Policy (#1373). Read at
  // build time, as `NEXT_PUBLIC_LIVE_URL` is read into the page code.
  async headers() {
    const policy = contentSecurityPolicy({
      development: process.env.NODE_ENV === "development",
      liveUrl: process.env.NEXT_PUBLIC_LIVE_URL,
    });
    return [{ source: "/:path*", headers: [{ key: "Content-Security-Policy", value: policy }] }];
  },
};

export default config;
