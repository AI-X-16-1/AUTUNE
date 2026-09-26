import { GapReportDemo } from "@/features/gap";
import { notFound } from "next/navigation";

/**
 * Temporary preview route for the S20 gap report, laid out the way
 * `docs/design/AUTUNE Spec 03 회의 후.dc.html` draws it.
 *
 * Not a real screen. `/meetings/{id}/gap` is, and it reads `/api/gap`; this one
 * mounts the same screen over a fixture meeting so the layout can be reviewed
 * without Postgres, the worker and a transcribed recording. Delete it once a
 * seeded meeting is a local command.
 *
 * `notFound()` in production makes the "temporary" claim a fact rather than a
 * promise a stale comment makes — the same reasoning as `/dev-dashboard` and
 * #241's default-off CORS.
 */
export default function DevGapPage() {
  if (process.env.NODE_ENV === "production") {
    notFound();
  }

  return <GapReportDemo />;
}
