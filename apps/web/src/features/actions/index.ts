/**
 * What this feature offers the app shell.
 *
 * A route file imports `@/features/actions` and nothing deeper: eslint's
 * `no-restricted-imports` blocks the two-segment form so that a page cannot
 * reach past this line into a component's file path.
 */
export { ActionItemsScreen } from "./components/ActionItemsScreen";
export { IntegrationSettingsScreen } from "./components/IntegrationSettingsScreen";
export { MaterialsScreen } from "./components/MaterialsScreen";
export { MeetingSummaryScreen } from "./components/MeetingSummaryScreen";
export { OwnTeamMeetingsNotice } from "./components/OwnTeamMeetingsNotice";
/** What the new-meeting form's agenda row may draft from (#1147). */
export { JIRA_AGENDA } from "./agendaSources";
export { TeamActionsScreen } from "./components/TeamActionsScreen";
