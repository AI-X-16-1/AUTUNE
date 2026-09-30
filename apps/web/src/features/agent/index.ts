/**
 * What the app shell may import from this feature.
 *
 * Routes under `apps/web/src/app` mount screens from here and nothing deeper;
 * eslint refuses `@/features/agent/<anything>/…`. Components and types stay
 * internal so a route cannot compose a screen the feature did not design.
 */
export { ApprovalsScreen } from "./components/ApprovalsScreen";
