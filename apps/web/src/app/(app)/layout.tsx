import { AppHeader } from "../_components/AppHeader";

/**
 * The app shell: top bar 56 with a hairline only — see docs/design/ui-spec.md
 * section 0. The sidebar the spec also describes is not here; it would list
 * settings screens that do not exist (S28-S30), and a nav of dead links is
 * worse than no nav.
 *
 * It wraps every screen a signed-in person sees and nothing else. `/login`
 * sits outside this group on purpose: its wordmark links to `/`, which is not
 * open to somebody who has not signed in yet, and that screen brings its own
 * header. The group changes no URL — `(app)` is a folder name Next.js does not
 * route on, the same device `(review)` uses one level down.
 *
 * It imposes no width on the page below it. Every screen already declares its
 * own reading width — 720 for a transcript, 1200 for the action board — and a
 * container here would either fight them or force them all to one number.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <AppHeader />
      {children}
    </>
  );
}
