/**
 * What this feature offers the app shell.
 *
 * A route file imports `@/features/gap` and nothing deeper: eslint's
 * `no-restricted-imports` blocks the two-segment form so that a page cannot
 * reach past this line into a component's file path. That boundary is what
 * lets the feature move its own files without touching the team's shared tree.
 */
export { GapReportScreen } from "./components";

/** The sidebar's "갭 리포트": a team's open gaps across its meetings (#550). */
export { TeamGapList } from "./components";

/**
 * S14's small cut (#1147): the band a live recording shows five minutes before
 * its planned end. The live screen is module A's; the route puts this in its
 * slot.
 */
export { EndAlertBand } from "./components";

/**
 * S20 with a fixture meeting behind it, for the temporary `/dev-gap` route.
 * Not part of the product: the route it serves 404s outside development.
 */
export { GapReportDemo } from "./components";
