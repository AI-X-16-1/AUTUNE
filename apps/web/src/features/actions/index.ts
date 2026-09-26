/**
 * What this feature offers the app shell.
 *
 * A route file imports `@/features/actions` and nothing deeper: eslint's
 * `no-restricted-imports` blocks the two-segment form so that a page cannot
 * reach past this line into a component's file path.
 */
export { ActionItemsScreen } from "./components/ActionItemsScreen";
