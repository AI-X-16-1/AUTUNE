import { IntegrationSettingsScreen } from "@/features/actions";

/**
 * S28, 설정 › 연동 (#496).
 *
 * Assembly only: the screen and the connect buttons it gathers live in
 * `features/actions`, module B's, which built them for the 액션 tab. This file
 * exists because a feature cannot give itself a route.
 */
export default function IntegrationSettingsPage() {
  return <IntegrationSettingsScreen />;
}
