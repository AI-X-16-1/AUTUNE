/**
 * "The assistant just changed something" -- told to every screen on the page.
 *
 * An L1 action asked for in chat runs on the server at once (a new weekly
 * report schedule, a re-drafted meeting report), and an L2 approved on a chat
 * card runs when it is approved. The screen behind the assistant loaded its
 * values before that, and showed the old ones until a reload, which also ends
 * the conversation (#1055). A screen that shows what the assistant can change
 * listens here and reads its own values again.
 *
 * The event carries nothing: not what ran, not its arguments, not the team. A
 * listener reads its own values, which the server checks as it always does
 * (#1055 review). It lives in `shared/` because features do not import one
 * another: the assistant (`features/agent`) announces, other features listen.
 */
const ACTED = "autune:agent-acted";

export function announceAgentActed(): void {
  window.dispatchEvent(new Event(ACTED));
}

/** Hear every announcement on this page. Returns the way to stop listening. */
export function onAgentActed(listener: () => void): () => void {
  const heard = () => listener();
  window.addEventListener(ACTED, heard);
  return () => window.removeEventListener(ACTED, heard);
}
