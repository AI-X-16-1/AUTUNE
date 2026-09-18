/**
 * What this feature offers the app shell.
 *
 * A route file imports `@/features/gap` and nothing deeper: eslint's
 * `no-restricted-imports` blocks the two-segment form so that a page cannot
 * reach past this line into a component's file path. That boundary is what
 * lets the feature move its own files without touching the team's shared tree.
 */
export { GapReportScreen } from "./components";
