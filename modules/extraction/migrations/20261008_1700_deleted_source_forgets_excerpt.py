"""A source row forgets its excerpt offsets when its utterance is deleted (#400)

``ext_action_item_sources`` and ``ext_decision_sources`` outlive the utterance
they pointed at with ``utterance_id`` NULL (``4f0b7d9e2c61``, ``9c4e7a1d5b28``),
so that a reader can be told a source existed. Since ``2b9e6d4f8a13`` such a
row can also hold ``excerpt_start`` and ``excerpt_end``: where in the
utterance's text the item or the decision was made from. Once the text is gone
nothing reads them, and they are still a trace of the deleted line -- its
length is at least ``excerpt_end``. They now go with it.

The database does it. Utterances are deleted by module A (a rerun of the
meeting, a person deleting their own speech, an account deletion) and this
module is told of none of them, so a rule kept in this module's code would
hold only on the paths someone remembered. A row trigger on each table clears
the two offsets whenever the row is written with no ``utterance_id`` -- which
is what ``ON DELETE SET NULL`` does to it.

Rows that lost their utterance before this revision are cleared here.

Downgrading drops the triggers and the function. The offsets that were cleared
are not restored: there is nothing to restore them from.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 5d1f8b3a7c46
Revises: 9c4e7a1d5b28
Create Date: 2026-10-08 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "5d1f8b3a7c46"
down_revision: str | None = "9c4e7a1d5b28"  # extraction: decision sources outlive utterances
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION = "ext_forget_excerpt_of_deleted_source"
TABLES = ("ext_action_item_sources", "ext_decision_sources")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {FUNCTION}() RETURNS trigger AS $$
        BEGIN
            NEW.excerpt_start := NULL;
            NEW.excerpt_end := NULL;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_forget_excerpt
            BEFORE INSERT OR UPDATE ON {table}
            FOR EACH ROW
            WHEN (NEW.utterance_id IS NULL)
            EXECUTE FUNCTION {FUNCTION}()
            """
        )
        # Fires the trigger for each row it touches, which sets the same thing.
        op.execute(
            f"""
            UPDATE {table} SET excerpt_start = NULL, excerpt_end = NULL
            WHERE utterance_id IS NULL
              AND (excerpt_start IS NOT NULL OR excerpt_end IS NOT NULL)
            """  # noqa: S608  (table names are the two constants above)
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TRIGGER {table}_forget_excerpt ON {table}")
    op.execute(f"DROP FUNCTION {FUNCTION}()")
