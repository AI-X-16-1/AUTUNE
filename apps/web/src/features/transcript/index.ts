/**
 * What the app shell may import from this feature.
 *
 * Routes under `apps/web/src/app` mount screens from here and nothing deeper;
 * eslint refuses `@/features/transcript/<anything>/…`. Components, hooks and
 * types stay internal so a route cannot compose a screen the feature did not
 * design.
 */
export { LiveMeetingScreen } from "./components/LiveMeetingScreen";
export { StoredMeetingScreen } from "./components/StoredMeetingScreen";
export { NewMeetingScreen } from "./components/NewMeetingScreen";
