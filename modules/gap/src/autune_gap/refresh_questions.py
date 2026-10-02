"""``python -m autune_gap.refresh_questions [--team TEAM_ID] [--dry-run]``.

Recomputes the stored ``suggested_question`` of every template gap with C's
own question logic (``detect.question_for``), so gaps raised before a missing
item's question named the meeting's subject read like the ones raised after.
See ``service.refresh_questions``: only the question changes; coverage, score
and severity are the pipeline's and are left as stored.

A meeting whose questions changed gets ``autune.gap.publish_report`` queued,
the same republish a dismissal or a template switch queues, so E's stored copy
of the report carries the same questions as ``/api/gap/reports`` and the
agent's ``gap.open_gaps``. So it needs the database and the broker; the worker
does the publish. Safe to run twice: a second run finds nothing to change and
queues nothing.

Prints counts and meeting ids only: a question names a topic label, which is
meeting content.
"""

from __future__ import annotations

import argparse

from autune_core import session_scope
from autune_core.celery_app import make_celery_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autune_gap.refresh_questions")
    parser.add_argument("--team", help="only this team's meetings")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="count the questions that would change, write nothing and queue nothing",
    )
    args = parser.parse_args(argv)

    # A client app on the real broker, so the queued republish reaches the
    # worker; imported after it so ``current_app`` is this one.
    make_celery_app(include_tasks=False)
    from autune_gap import service
    from autune_gap.enqueue import enqueue_publish_report

    with session_scope() as session:
        meeting_ids = service.refreshable_meeting_ids(session, team_id=args.team)
    print(f"{len(meeting_ids)} meeting(s) to check{' (dry run)' if args.dry_run else ''}")

    total = 0
    for index, meeting_id in enumerate(meeting_ids, start=1):
        changed = service.refresh_questions(meeting_id, apply=not args.dry_run)
        total += changed
        if changed and not args.dry_run:
            enqueue_publish_report(meeting_id)
        print(f"[{index}/{len(meeting_ids)}] {meeting_id} changed={changed}")
    print(f"{total} question(s) {'would change' if args.dry_run else 'changed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
