"""Write session-table rows to Parquet through DuckDB (ADR 0001).

The same atomic write as the run table's writer, with the session schema. The
table is created from an explicit schema so every column keeps its type even
when all its values are null, or when there are no rows at all.
"""

import dataclasses
from collections.abc import Sequence
from pathlib import Path

import duckdb

from agentic_telemetry.session_table.row import SessionRow

LIST_COLUMNS = frozenset({"models", "served_models"})
BOOLEAN_COLUMNS = frozenset({"is_subagent"})
DOUBLE_COLUMNS = frozenset({"span_seconds"})
BIGINT_COLUMNS = frozenset(
    {
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
    }
)


def _duckdb_type(column: str) -> str:
    """Return the DuckDB type of one session-table column."""
    if column in LIST_COLUMNS:
        return "VARCHAR[]"
    if column in BOOLEAN_COLUMNS:
        return "BOOLEAN"
    if column in DOUBLE_COLUMNS:
        return "DOUBLE"
    if column in BIGINT_COLUMNS:
        return "BIGINT"
    return "VARCHAR"


SESSION_TABLE_SCHEMA: tuple[tuple[str, str], ...] = tuple(
    (field.name, _duckdb_type(field.name)) for field in dataclasses.fields(SessionRow)
)


def write_session_table(rows: Sequence[SessionRow], out: Path) -> None:
    """Write `rows` to `out` as Parquet, replacing any existing file.

    The file is written to a sibling temporary path and moved into place, so a
    failed write never leaves a partial table at `out`.

    Args:
        rows: The session-table rows.
        out: The Parquet file to write; its parent directory must exist.

    Raises:
        duckdb.Error: DuckDB could not build or write the table.
        OSError: The file could not be moved into place.
    """
    temporary = out.with_name(f"{out.name}.tmp")
    column_defs = ", ".join(f"{name} {kind}" for name, kind in SESSION_TABLE_SCHEMA)
    placeholders = ", ".join("?" for _ in SESSION_TABLE_SCHEMA)
    connection = duckdb.connect()
    try:
        connection.execute(f"CREATE TABLE session_table ({column_defs})")
        if rows:
            connection.executemany(
                f"INSERT INTO session_table VALUES ({placeholders})",  # noqa: S608 - fixed column list
                [_row_values(row) for row in rows],
            )
        connection.execute("COPY session_table TO ? (FORMAT parquet)", [str(temporary)])
        temporary.replace(out)
    finally:
        connection.close()
        temporary.unlink(missing_ok=True)


def _row_values(row: SessionRow) -> list[object]:
    """Return a row's values in column order, with tuples as lists for VARCHAR[]."""
    values: list[object] = []
    for name, _ in SESSION_TABLE_SCHEMA:
        value = getattr(row, name)
        values.append(list(value) if isinstance(value, tuple) else value)
    return values
