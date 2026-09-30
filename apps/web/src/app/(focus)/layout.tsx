/**
 * Screens drawn without the app shell. S13, the live transcript, is the one
 * today: its design file has no sidebar and brings its own top bar, because a
 * meeting in progress is the only thing on screen.
 *
 * The group changes no URL — `/meetings/<id>/live` is where it always was; only
 * the frame around it differs from its siblings under `(app)`. Anything that
 * belongs in the sidebar shell goes there, not here.
 */
export default function FocusLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
