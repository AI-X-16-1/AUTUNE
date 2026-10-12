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
/**
 * What an agenda draft has from this module (#1147): the team's open Jira
 * issues, for the new-meeting form's row, and an earlier meeting's unfinished
 * to-dos, which the pre-meeting brief lists. `SINCE_LAST_MEETING_AGENDA` --
 * what moved on the team's board since its last meeting -- is offered and not
 * yet handed to either.
 */
export { EARLIER_ITEMS_AGENDA, JIRA_AGENDA, SINCE_LAST_MEETING_AGENDA } from "./agendaSources";
export { TeamActionsScreen } from "./components/TeamActionsScreen";
