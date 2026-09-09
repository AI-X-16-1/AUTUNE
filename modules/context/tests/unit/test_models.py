"""Schema-shape checks that need no database."""

from __future__ import annotations

import autune_context.models  # noqa: F401  (registers ctx_* tables on Base.metadata)
from autune_context.config import ContextSettings
from autune_context.constants import EMBEDDING_DIM
from autune_core import Base

CTX_TABLES = {
    "ctx_embeddings",
    "ctx_topic_links",
    "ctx_decisions",
    "ctx_decision_versions",
    "ctx_meeting_status",
}


def test_all_five_ctx_tables_are_registered():
    assert set(Base.metadata.tables) >= CTX_TABLES


def test_every_owned_table_carries_the_ctx_prefix():
    owned = set(Base.metadata.tables) - {
        "teams",
        "users",
        "team_members",
        "meetings",
        "participants",
        "utterances",
    }
    assert all(name.startswith("ctx_") for name in owned), owned


def test_every_ctx_table_has_a_deletion_path():
    for name in CTX_TABLES:
        columns = Base.metadata.tables[name].columns
        assert "meeting_id" in columns or "team_id" in columns, name


def test_no_foreign_key_points_at_another_modules_table():
    for name in CTX_TABLES:
        for fk in Base.metadata.tables[name].foreign_keys:
            target = fk.column.table.name
            assert target in {"meetings", "teams"} or target.startswith("ctx_"), (name, target)


def test_embedding_column_dimension_matches_the_constant():
    column = Base.metadata.tables["ctx_embeddings"].columns["embedding"]
    assert column.type.dim == EMBEDDING_DIM


def test_settings_default_dim_matches_the_column():
    # A bumped default with no migration would silently disagree with the DB.
    assert ContextSettings().embedding_dim == EMBEDDING_DIM
