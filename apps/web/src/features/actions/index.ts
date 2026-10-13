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
 * to-dos, which the pre-meeting brief lists. The to-dos that are late or were
 * closed without being finished go to both (#1270): `SINCE_LAST_MEETING_AGENDA`
 * for the form's row, `SINCE_EARLIER_MEETING_AGENDA` for the brief's draft.
 */
export {
  EARLIER_ITEMS_AGENDA,
  JIRA_AGENDA,
  SINCE_EARLIER_MEETING_AGENDA,
  SINCE_LAST_MEETING_AGENDA,
} from "./agendaSources";
export { TeamActionsScreen } from "./components/TeamActionsScreen";
