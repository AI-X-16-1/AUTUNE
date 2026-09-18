"""Schema-shape checks that need no database."""

from __future__ import annotations

from autune_context import models
from autune_context.config import ContextSettings
from autune_context.constants import EMBEDDING_DIM
from autune_core import Base

CTX_TABLES = {
    "ctx_embeddings",
    "ctx_topic_links",
    "ctx_decisions",
    "ctx_decision_versions",
    "ctx_meeting_status",
    "ctx_link_thresholds",
}
_MODEL_CLASSES = (
    models.CtxEmbedding,
    models.CtxTopicLink,
    models.CtxDecision,
    models.CtxDecisionVersion,
    models.CtxMeetingStatus,
    models.CtxLinkThreshold,
)


def test_every_ctx_table_is_registered():
    assert set(Base.metadata.tables) >= CTX_TABLES


def test_this_module_registers_only_ctx_prefixed_tables():
    # Every table any Ctx* model declares, prefixed; and no stray non-ctx table
    # slipped in among the ctx_-named ones.
    assert {m.__tablename__ for m in _MODEL_CLASSES} == CTX_TABLES
    assert all(name.startswith("ctx_") for name in CTX_TABLES)
    assert {t for t in Base.metadata.tables if t.startswith("ctx_")} == CTX_TABLES


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
