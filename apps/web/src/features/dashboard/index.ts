/**
 * What the app shell may import from this feature.
 *
 * Routes under `apps/web/src/app` mount screens from here and nothing deeper;
 * eslint refuses `@/features/dashboard/<anything>/…`.
 */
export { Dashboard } from "./components/Dashboard";
