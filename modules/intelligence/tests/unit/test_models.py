"""Module E schema — metadata assertions, no database."""

from __future__ import annotations

import autune_intelligence.models  # noqa: F401  (registers tables on Base.metadata)
from autune_core import Base

INTEL_TABLES = {
    "intel_completion",
    "intel_scores",
    "intel_gap_patterns",
    "intel_alignment",
    "intel_predictions",
    "intel_reports",
}


def _table(name: str):
    return Base.metadata.tables[name]


def test_module_owns_exactly_these_six_tables() -> None:
    present = {n for n in Base.metadata.tables if n.startswith("intel_")}
    assert present == INTEL_TABLES


def test_every_owned_table_carries_the_intel_prefix() -> None:
    for name in INTEL_TABLES:
        assert name.startswith("intel_")


def test_no_speaking_ratio_table_exists() -> None:
    for name in Base.metadata.tables:
        assert "speaking" not in name and "speech_ratio" not in name


def test_no_snapshot_table_exists() -> None:
    assert "intel_snapshots" not in Base.metadata.tables


def test_primary_keys_are_the_spec_natural_keys() -> None:
    expected = {
        "intel_completion": ["meeting_id"],
        "intel_scores": ["meeting_id"],
        "intel_gap_patterns": ["meeting_id", "pattern_type"],
        "intel_alignment": ["meeting_id", "role_a", "role_b"],
        "intel_predictions": ["meeting_id", "kind", "horizon_days"],
        "intel_reports": ["team_id", "period_start"],
    }
    for name, cols in expected.items():
        assert [c.name for c in _table(name).primary_key.columns] == cols


def test_meeting_scoped_tables_cascade_on_meeting_delete() -> None:
    for name in INTEL_TABLES - {"intel_reports"}:
        fk = next(iter(_table(name).c.meeting_id.foreign_keys))
        assert fk.column.table.name == "meetings"
        assert fk.ondelete == "CASCADE"


def test_reports_cascade_on_team_delete() -> None:
    fk = next(iter(_table("intel_reports").c.team_id.foreign_keys))
    assert fk.column.table.name == "teams"
    assert fk.ondelete == "CASCADE"


def test_no_foreign_key_points_at_another_modules_table() -> None:
    allowed = {"meetings", "teams", "users", "participants", "utterances"}
    for name in INTEL_TABLES:
        for col in _table(name).columns:
            for fk in col.foreign_keys:
                assert fk.column.table.name in allowed


def test_source_id_columns_have_no_foreign_key() -> None:
    assert not _table("intel_gap_patterns").c.source_gap_ids.foreign_keys


def test_check_constraints_are_present() -> None:
    def ck_names(table: str) -> set[str]:
        from sqlalchemy import CheckConstraint

        return {c.name for c in _table(table).constraints if isinstance(c, CheckConstraint)}

    assert "ck_intel_scores_grade" in ck_names("intel_scores")
    assert "ck_intel_scores_value_unit" in ck_names("intel_scores")
    assert "ck_intel_alignment_score_unit" in ck_names("intel_alignment")
    assert "ck_intel_predictions_probability_unit" in ck_names("intel_predictions")
    assert "ck_intel_predictions_horizon_positive" in ck_names("intel_predictions")


def test_team_id_is_indexed_on_every_dashboard_table() -> None:
    for name in {"intel_scores", "intel_gap_patterns", "intel_alignment", "intel_predictions"}:
        indexed = {c.name for idx in _table(name).indexes for c in idx.columns}
        assert "team_id" in indexed


def test_json_columns_are_jsonb() -> None:
    from sqlalchemy.dialects.postgresql import JSONB

    assert isinstance(_table("intel_scores").c.missing_sources.type, JSONB)
    assert isinstance(_table("intel_reports").c.metrics_json.type, JSONB)
    assert isinstance(_table("intel_reports").c.source_meeting_ids.type, JSONB)
    assert isinstance(_table("intel_gap_patterns").c.source_gap_ids.type, JSONB)


def test_intel_completion_stages_each_upstream_payload() -> None:
    columns = set(_table("intel_completion").columns.keys())
    assert {"extraction_payload", "gap_payload", "context_payload"} <= columns


def test_the_payload_columns_are_jsonb() -> None:
    from sqlalchemy.dialects.postgresql import JSONB

    for name in ("extraction_payload", "gap_payload", "context_payload"):
        assert isinstance(_table("intel_completion").c[name].type, JSONB)
