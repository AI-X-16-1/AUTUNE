import type { NextConfig } from "next";

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
};

export default config;
