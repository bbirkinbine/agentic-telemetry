"""Collapse a run reached through several inputs into one row, or refuse to."""

import dataclasses
from collections.abc import Sequence

from agentic_telemetry.run_table.row import RunRow

# Where a row was read from; the same run through two inputs differs only here.
LOCATION_FIELDS = ("project", "ledger_path")


class ConflictingRunError(Exception):
    """The same run ID carries different data in two places."""

    def __init__(self, run_id: str, first: RunRow, second: RunRow) -> None:
        """Record the run and both ledgers, naming the columns that differ."""
        self.run_id = run_id
        differing = ", ".join(_differing_fields(first, second))
        super().__init__(
            f"run {run_id} differs between {first.ledger_path} and {second.ledger_path} "
            f"(columns: {differing}); refusing to choose one"
        )


def merge_runs(rows: Sequence[RunRow]) -> list[RunRow]:
    """Keep one row per run ID, in first-seen order.

    Rows with the same run ID collapse when they match on every column except
    `project` and `ledger_path`; the first one is kept.

    Args:
        rows: Rows from every ledger, in discovery order.

    Returns:
        One row per distinct run ID.

    Raises:
        ConflictingRunError: Two rows with the same run ID differ in another column.
    """
    kept: dict[str, RunRow] = {}
    for row in rows:
        first = kept.get(row.run_id)
        if first is None:
            kept[row.run_id] = row
        elif _differing_fields(first, row):
            raise ConflictingRunError(row.run_id, first, row)
    return list(kept.values())


def _differing_fields(first: RunRow, second: RunRow) -> list[str]:
    """Return the non-location columns whose values differ between two rows."""
    return [
        field.name
        for field in dataclasses.fields(RunRow)
        if field.name not in LOCATION_FIELDS
        and getattr(first, field.name) != getattr(second, field.name)
    ]
