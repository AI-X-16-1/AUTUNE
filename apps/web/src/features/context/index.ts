/**
 * What this feature offers the app shell.
 *
 * A route file imports `@/features/context` and nothing deeper: eslint's
 * `no-restricted-imports` blocks the two-segment form so that a page cannot
 * reach past this line into a component's file path. Same boundary as
 * `features/transcript` and `features/gap`.
 */
export { ContextTab } from "./components/ContextTab";
export { DecisionLineagePanel } from "./components/DecisionLineagePanel";
