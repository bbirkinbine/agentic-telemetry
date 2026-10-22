"""Write run-table rows to Parquet through DuckDB (ADR 0001).

The table is created from an explicit schema so every column keeps its type
even when all its values are null, or when there are no rows at all. The
session table's writer (`session_table/parquet_writer.py`) repeats this pattern
with its own schema.
"""

import dataclasses
from collections.abc import Sequence
from pathlib import Path

import duckdb

from agentic_telemetry.run_table.row import RunRow

LIST_COLUMNS = frozenset(
    {
        "unverified_steps",
        "attempt_outcomes",
        "requested_models",
        "served_models",
        "subagent_models",
    }
)
BOOLEAN_COLUMNS = frozenset({"qualified", "server_cold_start"})
# Seconds can be fractional in some harness versions, so they are DOUBLE.
DOUBLE_COLUMNS = frozenset(
    {"agent_seconds", "wall_seconds", "server_prefill_tps", "server_decode_tps"}
)
BIGINT_COLUMNS = frozenset(
    {
        "attempts",
        "review_count",
        "tokens_in",
        "tokens_out",
        "tokens_cache_read",
        "tokens_reasoning",
        "assistant_turns",
        "compactions",
        "retries",
        "peak_turn_prompt_tokens",
        "subagent_runs",
        "subagent_turns",
        "server_prompt_tokens",
        "server_predicted_tokens",
        "server_deferred_max",
        # A high-water mark since the model loaded, not this run's own context.
        "server_ctx_high_water",
    }
)


def _duckdb_type(column: str) -> str:
    """Return the DuckDB type of one run-table column."""
    if column in LIST_COLUMNS:
        return "VARCHAR[]"
    if column in BOOLEAN_COLUMNS:
        return "BOOLEAN"
    if column in DOUBLE_COLUMNS:
        return "DOUBLE"
    if column in BIGINT_COLUMNS:
        return "BIGINT"
    return "VARCHAR"


RUN_TABLE_SCHEMA: tuple[tuple[str, str], ...] = tuple(
    (field.name, _duckdb_type(field.name)) for field in dataclasses.fields(RunRow)
)


def write_run_table(rows: Sequence[RunRow], out: Path) -> None:
    """Write `rows` to `out` as Parquet, replacing any existing file.

    The file is written to a sibling temporary path and moved into place, so a
    failed write never leaves a partial table at `out`.

    Args:
        rows: The run-table rows.
        out: The Parquet file to write; its parent directory must exist.

    Raises:
        duckdb.Error: DuckDB could not build or write the table.
        OSError: The file could not be moved into place.
    """
    temporary = out.with_name(f"{out.name}.tmp")
    column_defs = ", ".join(f"{name} {kind}" for name, kind in RUN_TABLE_SCHEMA)
    placeholders = ", ".join("?" for _ in RUN_TABLE_SCHEMA)
    connection = duckdb.connect()
    try:
        connection.execute(f"CREATE TABLE run_table ({column_defs})")
        if rows:
            connection.executemany(
                f"INSERT INTO run_table VALUES ({placeholders})",  # noqa: S608 - fixed column list
                [_row_values(row) for row in rows],
            )
        connection.execute("COPY run_table TO ? (FORMAT parquet)", [str(temporary)])
        temporary.replace(out)
    finally:
        connection.close()
        temporary.unlink(missing_ok=True)


def _row_values(row: RunRow) -> list[object]:
    """Return a row's values in column order, with tuples as lists for VARCHAR[]."""
    values: list[object] = []
    for name, _ in RUN_TABLE_SCHEMA:
        value = getattr(row, name)
        values.append(list(value) if isinstance(value, tuple) else value)
    return values
