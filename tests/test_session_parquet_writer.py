"""Session-table Parquet: spec column order, explicit types, nulls, empty table, atomic replace."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import duckdb
import pytest
from session_tree_builder import session_row_values

from agentic_telemetry.session_table.parquet_writer import (
    SESSION_TABLE_SCHEMA,
    write_session_table,
)
from agentic_telemetry.session_table.row import SessionRow

EXPECTED_COLUMNS = [
    "client",
    "session_id",
    "source_path",
    "project",
    "cwd",
    "client_version",
    "git_branch",
    "is_subagent",
    "parent_session_id",
    "run_id",
    "started_at",
    "ended_at",
    "span_seconds",
    "provider",
    "models",
    "primary_model",
    "served_models",
    "primary_served_model",
    "tokens_in",
    "tokens_cache_read",
    "tokens_cache_write",
    "tokens_out",
    "tokens_reasoning",
    "assistant_turns",
    "user_prompts",
    "tool_calls",
    "tool_errors",
    "compactions",
    "stop_reasons",
]

BIGINT_COLUMNS = [
    "tokens_in",
    "tokens_cache_read",
    "tokens_cache_write",
    "tokens_out",
    "tokens_reasoning",
    "assistant_turns",
    "user_prompts",
    "tool_calls",
    "tool_errors",
    "compactions",
]

VARCHAR_COLUMNS = [
    "client",
    "session_id",
    "source_path",
    "project",
    "cwd",
    "client_version",
    "git_branch",
    "parent_session_id",
    "run_id",
    "started_at",
    "ended_at",
    "provider",
    "primary_model",
    "primary_served_model",
    "stop_reasons",
]


def sample_row(session_id: str = "pi-main-0001", **overrides: object) -> SessionRow:
    return SessionRow(**session_row_values(session_id=session_id, **overrides))


def _read(path: Path) -> tuple[list[str], dict[str, str], list[dict[str, object]]]:
    relation = duckdb.read_parquet(str(path))
    columns = list(relation.columns)
    types = {name: str(kind) for name, kind in zip(columns, relation.types, strict=True)}
    records = [dict(zip(columns, values, strict=True)) for values in relation.fetchall()]
    return columns, types, records


def test_session_row_fields_are_the_spec_column_order() -> None:
    assert [field.name for field in dataclasses.fields(SessionRow)] == EXPECTED_COLUMNS


def test_session_row_is_frozen() -> None:
    row = sample_row()

    with pytest.raises(dataclasses.FrozenInstanceError):
        row.session_id = "changed"  # type: ignore[misc]


def test_schema_constant_matches_session_row_fields() -> None:
    assert [name for name, _ in SESSION_TABLE_SCHEMA] == EXPECTED_COLUMNS


def test_schema_constant_declares_the_spec_types() -> None:
    schema = dict(SESSION_TABLE_SCHEMA)

    assert schema["models"] == "VARCHAR[]"
    assert schema["served_models"] == "VARCHAR[]"
    assert schema["is_subagent"] == "BOOLEAN"
    assert schema["span_seconds"] == "DOUBLE"
    for name in BIGINT_COLUMNS:
        assert schema[name] == "BIGINT", name
    for name in VARCHAR_COLUMNS:
        assert schema[name] == "VARCHAR", name


def test_written_file_has_exact_columns_in_order_and_one_row_per_session(tmp_path: Path) -> None:
    out = tmp_path / "session_table.parquet"

    write_session_table([sample_row("s-a"), sample_row("s-b")], out)

    columns, _, records = _read(out)
    assert columns == EXPECTED_COLUMNS
    assert [record["session_id"] for record in records] == ["s-a", "s-b"]


def test_values_round_trip_with_lists_bools_doubles_and_json_text(tmp_path: Path) -> None:
    out = tmp_path / "session_table.parquet"

    write_session_table([sample_row("s-a", is_subagent=True, span_seconds=90.5)], out)

    _, _, (record,) = _read(out)
    assert record["models"] == ["example-coder", "example-reasoner"]
    assert record["served_models"] == ["example-coder-q8", "example-reasoner-q4"]
    assert record["is_subagent"] is True
    assert record["span_seconds"] == 90.5
    assert record["tokens_in"] == 300
    assert isinstance(record["stop_reasons"], str)
    assert json.loads(record["stop_reasons"]) == {"stop": 2, "toolUse": 1}
    assert record["client_version"] is None


def test_column_types_in_the_file_are_fixed_by_the_schema(tmp_path: Path) -> None:
    out = tmp_path / "session_table.parquet"

    write_session_table([sample_row()], out)

    _, types, _ = _read(out)
    assert types["models"] == "VARCHAR[]"
    assert types["is_subagent"] == "BOOLEAN"
    assert types["span_seconds"] == "DOUBLE"
    for name in BIGINT_COLUMNS:
        assert types[name] == "BIGINT", name
    for name in VARCHAR_COLUMNS:
        assert types[name] == "VARCHAR", name


def test_all_null_columns_keep_their_types(tmp_path: Path) -> None:
    out = tmp_path / "session_table.parquet"
    nulls = {name: None for name in [*BIGINT_COLUMNS, "span_seconds", "stop_reasons", "provider"]}

    write_session_table([sample_row(models=(), primary_model=None, **nulls)], out)

    _, types, (record,) = _read(out)
    assert record["tokens_in"] is None
    assert record["models"] == []
    assert types["tokens_in"] == "BIGINT"
    assert types["span_seconds"] == "DOUBLE"
    assert types["models"] == "VARCHAR[]"
    assert types["stop_reasons"] == "VARCHAR"


def test_empty_table_still_writes_the_full_schema(tmp_path: Path) -> None:
    out = tmp_path / "session_table.parquet"

    write_session_table([], out)

    columns, types, records = _read(out)
    assert columns == EXPECTED_COLUMNS
    assert records == []
    assert types["models"] == "VARCHAR[]"
    assert types["is_subagent"] == "BOOLEAN"
    assert types["tool_calls"] == "BIGINT"


def test_existing_output_file_is_replaced(tmp_path: Path) -> None:
    out = tmp_path / "session_table.parquet"
    write_session_table([sample_row("s-a"), sample_row("s-b"), sample_row("s-c")], out)

    write_session_table([sample_row("s-z")], out)

    _, _, records = _read(out)
    assert [record["session_id"] for record in records] == ["s-z"]


def test_write_leaves_only_the_output_file_behind(tmp_path: Path) -> None:
    out_dir = tmp_path / "data"
    out_dir.mkdir()
    out = out_dir / "session_table.parquet"

    write_session_table([sample_row()], out)

    assert sorted(path.name for path in out_dir.iterdir()) == ["session_table.parquet"]
