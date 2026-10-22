"""Shared setup for the capture-to-session join tests (spec 0008: Per-session view).

Interfaces the join tests pin:

- `read_session_windows(path) -> list[SessionWindow]` from
  `captures.session_windows` reads a session-table Parquet.
- `assign_calls(records, sessions) -> list[ClaimedCall]` from `captures.join`
  takes harvested records (the plan's dicts, as `read_records` returns them) and
  returns one `ClaimedCall` per record, in input order. A `ClaimedCall` has
  `record` (the input dict), `session_ids` (the claiming sessions; empty when
  unclaimed) and `flags` (a collection of `"ambiguous"` / `"header_unmatched"`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from capture_builders import (
    ALIAS_QWEN,
    QWEN,
    activity_row,
    capture,
    chat_request,
    record,
    write_sessions,
)

from agentic_telemetry.captures.join import ClaimedCall, assign_calls
from agentic_telemetry.captures.session_windows import read_session_windows
from agentic_telemetry.session_table.row import SessionRow


def call(
    row_id: int,
    end: datetime,
    *,
    model: str = QWEN,
    requested: str = ALIAS_QWEN,
    duration_ms: int = 2000,
    headers: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return a saved record: llama-swap model `model`, request body model `requested`."""
    row = activity_row(row_id, end, model=model, duration_ms=duration_ms)
    body = chat_request(requested)
    return record(row, capture(row_id, request=body, session_headers=headers))


def claims(
    tmp_path: Path, sessions: Sequence[SessionRow], records: Sequence[dict[str, object]]
) -> dict[int, ClaimedCall]:
    """Write `sessions` to Parquet, read them back as windows, join, and key by activity id."""
    windows = read_session_windows(write_sessions(tmp_path / "session_table.parquet", sessions))
    result = assign_calls(records, windows)
    assert len(result) == len(records), "every call is listed, claimed or not"
    return {int(str(item.record["activity"]["id"])): item for item in result}  # type: ignore[index]


def owners(item: ClaimedCall) -> set[str]:
    """Return the claiming session ids as a set."""
    return set(item.session_ids)


def seconds(value: int) -> timedelta:
    """Shorthand for a timedelta of `value` seconds."""
    return timedelta(seconds=value)
