"""What module C's tables hold, and what they must never hold.

No database. Every rule here is a fact about the table definitions, so the
schema can be argued with in review without anyone starting Postgres.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import Table

from autune_contracts.enums import GapSeverity
from autune_gap.models import (
    GapGap,
    GapMeetingTemplate,
    GapParticipation,
    GapRelatedTopic,
    GapTopic,
    GapTopicEdge,
    GapTopicUtterance,
)

ALL_TABLES: tuple[Table, ...] = (
    GapTopic.__table__,
    GapTopicUtterance.__table__,
    GapTopicEdge.__table__,
    GapParticipation.__table__,
    GapGap.__table__,
    GapRelatedTopic.__table__,
    GapMeetingTemplate.__table__,
)


# --- the participation matrix is coverage, not volume -----------------------


def test_participation_records_whether_not_how_much() -> None:
    """Invariant 11 and privacy.md section 3, made structural.

    A speaking ratio belongs to the speaker alone, and this table reaches the
    whole team inside a gap report. The column set is asserted whole rather
    than the absence of one name, so a ``duration_sec`` or ``utterance_count``
    added later fails here, where the reason is written down, instead of
    passing review as an obvious convenience.
    """
    columns = {column.name for column in GapParticipation.__table__.columns}

    assert columns == {"id", "topic_id", "participant_id", "spoke"}


def test_the_participation_flag_is_a_boolean() -> None:
    """A number in this column is a talk-time metric whatever it is called."""
    assert GapParticipation.__table__.c.spoke.type.python_type is bool


def test_participation_matches_the_contract_it_is_published_as() -> None:
    """``autune_contracts.Participation`` carries ids and no number.

    The table and the contract are two statements of one rule, and a column the
    contract has no field for is a column somebody is about to add a field for.
    """
    from autune_contracts.gap import Participation

    assert set(Participation.model_fields) == {"topic_id", "spoke", "silent"}


# --- no copies of transcript text -------------------------------------------


@pytest.mark.parametrize("table", ALL_TABLES, ids=lambda t: t.name)
def test_no_table_copies_utterance_text(table: Table) -> None:
    """The quotation is read by joining ``utterances``.

    Denormalising it would leave transcript content behind a cascade that no
    longer reaches it — the same rule module B pins on ``ext_decision_sources``
    and module D had to add a sweep for (``previous_statement``, PR #90).
    """
    columns = {column.name for column in table.columns}

    assert not {"text", "utterance_text", "quoted_text", "transcript"} & columns


# --- a row can name the model that produced it ------------------------------


def test_a_topic_records_which_extractor_built_it() -> None:
    """Three docstrings promise "recorded with the rows this produces", and the
    promise needs a column to be true.

    C's metric is gap precision over time and dismissals feed threshold tuning;
    both read across model versions. A row that cannot name its extractor takes
    part in neither, and after #13 lands a tuning pass would be mixing two
    models without knowing it.
    """
    assert "extractor_version" in GapTopic.__table__.c
    assert GapTopic.__table__.c.extractor_version.nullable is False


def test_the_extractor_version_is_required_from_the_start() -> None:
    """Not added later. A column added afterwards leaves every row written
    before it unattributable forever, and those are the rows the first tuning
    pass would read."""
    assert GapTopic.__table__.c.extractor_version.default is None
    assert GapTopic.__table__.c.extractor_version.server_default is None


def test_a_topic_points_at_its_evidence_by_id() -> None:
    columns = {column.name for column in GapTopicUtterance.__table__.columns}

    assert columns == {"id", "topic_id", "utterance_id", "position"}


# --- everything is deleted with its meeting ---------------------------------


@pytest.mark.parametrize("table", ALL_TABLES, ids=lambda t: t.name)
def test_every_table_cascades_towards_a_meeting(table: Table) -> None:
    """data-model.md: every module table needs a path to deletion by meeting.

    Either the table references ``meetings`` directly or it hangs off one that
    does, and every hop is ``CASCADE`` — one ``SET NULL`` in the chain would
    strand the rows below it.
    """
    parents = {"meetings", "gap_topics", "gap_gaps", "utterances", "participants"}
    foreign_keys = list(table.foreign_keys)

    assert foreign_keys, f"{table.name} has no path to a meeting"
    assert all(fk.column.table.name in parents for fk in foreign_keys)
    assert all(fk.ondelete == "CASCADE" for fk in foreign_keys)


def test_no_table_references_another_modules_tables() -> None:
    """Invariant 2 and data-model.md: a cross-module FK couples two Alembic
    branches. An id from another module is stored as a plain string."""
    other_prefixes = ("ext_", "aud_", "ctx_", "intel_")

    for table in ALL_TABLES:
        referenced = {fk.column.table.name for fk in table.foreign_keys}
        assert not any(name.startswith(other_prefixes) for name in referenced)


def test_every_table_carries_the_module_prefix() -> None:
    """Invariant 3. A table without the prefix is a shared entity."""
    assert all(table.name.startswith("gap_") for table in ALL_TABLES)


# --- ids are the ones the contracts require ---------------------------------


def mint(table: Table) -> str:
    """The id this table would generate on insert.

    Read off the column default rather than by inserting a row: the default is
    where the prefix is decided, and reaching it needs no database.
    """
    return str(table.c.id.default.arg(None))


def test_a_topic_id_matches_the_contract_pattern() -> None:
    """``autune_contracts.Topic.id`` is ``^topic_``. A mismatch is a validation
    error at publish time, on a real meeting, after the work is done."""
    assert mint(GapTopic.__table__).startswith("topic_")


def test_a_gap_id_matches_the_contract_pattern() -> None:
    assert mint(GapGap.__table__).startswith("gap_")


def test_each_row_gets_its_own_id() -> None:
    assert mint(GapTopic.__table__) != mint(GapTopic.__table__)


# --- what the constraints refuse --------------------------------------------


def test_severity_accepts_exactly_the_contract_enum() -> None:
    """The check constraint and ``GapSeverity`` are two statements of one rule.

    A level added on one side and not the other fails at insert time, in
    production, on a meeting somebody is waiting for.
    """
    constraint = next(
        c for c in GapGap.__table__.constraints if getattr(c, "name", "") == "ck_gap_gaps_severity"
    )
    accepted = set(re.findall(r"'(\w+)'", str(constraint.sqltext)))  # type: ignore[attr-defined]

    assert accepted == {level.value for level in GapSeverity}


def test_an_edge_cannot_point_at_itself() -> None:
    """A self-loop makes PageRank concentrate on the node that has one, and it
    is never a relation the extractor meant to produce."""
    names = {c.name for c in GapTopicEdge.__table__.constraints}

    assert "ck_gap_topic_edges_no_self_loop" in names


def test_one_relation_between_two_topics_is_stored_once() -> None:
    """The pipeline is re-run on the same meeting; without this the graph grows
    a parallel edge per run and centrality drifts with the retry count."""
    names = {c.name for c in GapTopicEdge.__table__.constraints}

    assert "uq_gap_topic_edges" in names


def test_a_participant_has_one_verdict_per_topic() -> None:
    names = {c.name for c in GapParticipation.__table__.constraints}

    assert "uq_gap_participation" in names


def test_the_dismissal_is_a_mark_not_a_soft_delete() -> None:
    """data-model.md forbids soft deletes. ``dismissed_at`` does not hide the
    row — threshold tuning has to be able to read what was dismissed."""
    columns = {column.name for column in GapGap.__table__.columns}

    assert "dismissed_at" in columns
    assert not {"deleted_at", "is_deleted", "hidden"} & columns


def test_a_dismissal_records_no_person() -> None:
    """ADR 0003: no per-person record of one person's conduct in a meeting.

    Which teammate pressed dismiss is not something threshold tuning needs.
    """
    columns = {column.name for column in GapGap.__table__.columns}

    assert not {"dismissed_by", "dismissed_by_user_id", "user_id"} & columns


def test_every_meeting_id_column_is_indexed() -> None:
    """data-model.md, "Conventions": you will query by meeting."""
    for table in ALL_TABLES:
        if "meeting_id" not in table.c:
            continue
        if table.c.meeting_id.primary_key:
            # Already indexed, by being the key. `gap_meeting_template` is one
            # row per meeting, so the lookup this rule is about is the primary
            # key lookup; a second index on the same column would be a write
            # every insert pays for and no read uses.
            continue
        indexed = {tuple(c.name for c in index.columns) for index in table.indexes}
        assert ("meeting_id",) in indexed, table.name


# --- the models and the migrations describe the same tables -----------------


def migration_tables() -> dict[str, set[str]]:
    """Table -> column names, as this module's migrations leave them.

    Text, not a database. The round-trip test needs Postgres and a person
    without Docker cannot run it, so the failure this catches — a column added
    to a model and not to the migration — would otherwise reach CI. Reading the
    migration files costs nothing and catches it in the unit run.

    Revisions are read in filename order, which is the order they apply: the
    date prefix is what makes the two the same thing. ``op.add_column`` counts
    as much as ``op.create_table``, because a column added by a later revision
    is a column the database has — and reading only ``create_table`` made the
    first such column (``gap_topic_edges.extractor_version``, #32) look like a
    model with no migration behind it.

    A column added to a table no model here describes is not silently accepted
    either: it shows up as a key, and ``test_no_migration_creates_a_table_no_
    model_describes`` fails on it. Another module's table is another module's
    to migrate (invariant 10).
    """
    migrations = Path(__file__).resolve().parents[2] / "migrations"
    tables: dict[str, set[str]] = {}
    for path in sorted(migrations.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r'op\.create_table\(\s*"(\w+)",(.*?)\n    \)', source, re.S):
            tables[match.group(1)] = set(
                re.findall(r'sa\.Column\(\s*\n?\s*"(\w+)"', match.group(2))
            )
        # A column added to an existing table is as real as one the create
        # statement declared, and adding one is the normal way a table grows
        # after its first revision. Reading only `create_table` let a model grow
        # a column with no migration behind it at all, which is the single thing
        # this test exists to catch. `downgrade` drops rather than adds, so
        # scanning the whole file picks up no reversal.
        for table, column in re.findall(
            r'op\.add_column\(\s*\n?\s*"(\w+)",\s*\n?\s*sa\.Column\(\s*\n?\s*"(\w+)"', source
        ):
            tables.setdefault(table, set()).add(column)
    return tables


@pytest.mark.parametrize("table", ALL_TABLES, ids=lambda t: t.name)
def test_the_migration_creates_exactly_what_the_model_declares(table: Table) -> None:
    """A model column with no migration behind it exists only in tests.

    It passes every assertion in this file, and then the first insert against a
    real database fails on a column that is not there.
    """
    in_migration = migration_tables().get(table.name)

    assert in_migration is not None, f"{table.name} has no create_table"
    assert {column.name for column in table.columns} == in_migration


def test_no_migration_creates_a_table_no_model_describes() -> None:
    """The autogenerate trap (migrations.md): a stale local database makes
    autogenerate write ``create_table`` for tables this module does not own."""
    declared = {table.name for table in ALL_TABLES}

    assert set(migration_tables()) == declared
