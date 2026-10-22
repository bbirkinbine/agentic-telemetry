"""Join run-table rows to the inference host's per-model metrics (spec 0005).

One pass over the table: read the known model IDs once, read each run's window,
resolve the model it ran on, fill the `server_*` columns, then mark runs that
shared a model and a window as `overlap`. Nothing is apportioned between
overlapping runs; their columns stay filled so a reader can see both and judge
the run table's own coverage.

The queries are read-only. A failure of the known-models read stops the whole
join: without it every row would resolve by counters alone, which is a different
table from the one that was asked for.
"""

import dataclasses
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from agentic_telemetry.run_table.row import RunRow
from agentic_telemetry.server_metrics.columns import (
    JOIN_STATUSES,
    SERVER_MODEL_SOURCES,
    SERVER_STATUSES,
    STATUS_ERROR,
    STATUS_NO_DATA,
    STATUS_OVERLAP,
    ServerColumns,
    server_columns,
    unread,
)
from agentic_telemetry.server_metrics.http_fetch import (
    Fetch,
    MetricsQueryError,
    MetricsUnreachableError,
)
from agentic_telemetry.server_metrics.readings import read_window
from agentic_telemetry.server_metrics.resolve import SOURCE_NONE, resolve_model
from agentic_telemetry.server_metrics.victoria_client import (
    LABEL_VALUES_PATH,
    known_model_ids,
)
from agentic_telemetry.server_metrics.window import (
    SCRAPE_INTERVAL_SECONDS,
    RunWindow,
    overlaps,
    run_window,
    widen,
)

# VictoriaMetrics hides samples this close to now (`-search.latencyOffset`, 30 s,
# plus one scrape), so a run whose widened window ends inside this span needs a
# later rerun.
HIDDEN_TAIL_SECONDS = 60


@dataclass(frozen=True)
class ServerJoinReport:
    """Warnings and counts from one join pass."""

    warnings: tuple[str, ...] = ()
    status_counts: Counter[str] = field(default_factory=Counter)
    source_counts: Counter[str] = field(default_factory=Counter)


def join_server_metrics(
    rows: Sequence[RunRow],
    base_url: str,
    fetch: Fetch,
    now: datetime,
) -> tuple[list[RunRow], ServerJoinReport]:
    """Return `rows` with their server columns filled, and what the pass saw.

    Args:
        rows: Run-table rows, in the order they were built.
        base_url: The VictoriaMetrics base URL, without a trailing slash.
        fetch: The HTTP seam.
        now: The current aware UTC time, used for the known-models range and for
            warning about runs too recent for VictoriaMetrics to have published.

    Returns:
        The rows, in the same order, and the report.

    Raises:
        MetricsUnreachableError: The endpoint could not be reached, or the
            known-models read failed; the caller writes no table.
    """
    windows = [_window(row) for row in rows]
    known = _known_model_ids(base_url, fetch, windows, now)
    warnings: list[str] = []
    columns: list[ServerColumns] = []
    for row, window in zip(rows, windows, strict=True):
        if window is None:
            warnings.append(
                f"run {row.run_id}: no usable time window from ts, wall_seconds or "
                "agent_seconds; left as no_data"
            )
            columns.append(unread(STATUS_NO_DATA, None, SOURCE_NONE))
            continue
        if _may_be_hidden(window, now):
            warnings.append(
                f"run {row.run_id}: it ended within 90 s of now, so "
                "VictoriaMetrics' -search.latencyOffset plus one scrape may still hide its "
                "last samples; rerun this command later"
            )
        columns.append(_read_row(row, window, base_url, fetch, known, warnings))
    joined = _mark_overlaps(columns, windows)
    out_rows = [_apply(row, row_columns) for row, row_columns in zip(rows, joined, strict=True)]
    return out_rows, _report(warnings, out_rows)


def _read_row(
    row: RunRow,
    window: RunWindow,
    base_url: str,
    fetch: Fetch,
    known: frozenset[str],
    warnings: list[str],
) -> ServerColumns:
    """Read one run's window and resolve its model, or record why it could not."""
    try:
        readings = read_window(base_url, fetch, window)
    except MetricsQueryError as error:
        warnings.append(f"run {row.run_id}: query failed at {error.path}: {error.detail}")
        return unread(STATUS_ERROR, None, SOURCE_NONE)
    model_id, source = resolve_model(
        row.requested_models or (),
        row.served_models or (),
        row.model_id,
        known,
        readings.moved_models(),
    )
    return server_columns(readings, model_id, source, row.tokens_in, row.tokens_out)


def _window(row: RunRow) -> RunWindow | None:
    """The window a row records for itself, or None when it records none."""
    return run_window(row.run_id, row.ts, row.wall_seconds, row.agent_seconds)


def _may_be_hidden(window: RunWindow, now: datetime) -> bool:
    """True when the window's widened end is close enough to now to be incomplete."""
    widened_end = widen(window, SCRAPE_INTERVAL_SECONDS).end
    return widened_end > now - timedelta(seconds=HIDDEN_TAIL_SECONDS)


def _known_model_ids(
    base_url: str,
    fetch: Fetch,
    windows: Sequence[RunWindow | None],
    now: datetime,
) -> frozenset[str]:
    """Read every model the inference host has ever served, once per invocation.

    The range starts one scrape before the earliest run in the table, so a run
    older than the agent still names a model and lands in `no_data` rather than
    `uncovered`.

    Raises:
        MetricsUnreachableError: The read failed; a join without the known set
            would resolve every run by counters alone.
    """
    starts = [window.start for window in windows if window is not None]
    start = (
        min(starts) - timedelta(seconds=SCRAPE_INTERVAL_SECONDS)
        if starts
        else now - timedelta(days=1)
    )
    try:
        return known_model_ids(base_url, fetch, start=start, end=now)
    except MetricsQueryError as error:
        raise MetricsUnreachableError(f"{LABEL_VALUES_PATH}: {error.detail}") from error


def _mark_overlaps(
    columns: Sequence[ServerColumns], windows: Sequence[RunWindow | None]
) -> list[ServerColumns]:
    """Mark runs that shared a served model and an unwidened window as `overlap`.

    Overlap compares the runs' own windows: widened windows touch whenever two
    runs are one scrape apart, which would mark a whole sequence of runs against
    each other. Nothing is apportioned; only the status changes.
    """
    marked = list(columns)
    for first in range(len(marked)):
        for second in range(first + 1, len(marked)):
            if _shares_traffic(marked[first], marked[second], windows[first], windows[second]):
                marked[first] = dataclasses.replace(marked[first], server_status=STATUS_OVERLAP)
                marked[second] = dataclasses.replace(marked[second], server_status=STATUS_OVERLAP)
    return marked


def _shares_traffic(
    first: ServerColumns,
    second: ServerColumns,
    first_window: RunWindow | None,
    second_window: RunWindow | None,
) -> bool:
    """True when two rows read the same model over windows that overlapped."""
    if first.server_model_id is None or first.server_model_id != second.server_model_id:
        return False
    if first.server_status not in JOIN_STATUSES or second.server_status not in JOIN_STATUSES:
        return False
    if first_window is None or second_window is None:
        return False
    return overlaps(first_window, second_window)


def _apply(row: RunRow, columns: ServerColumns) -> RunRow:
    """Return `row` with the server columns replaced; every other column is untouched.

    `ServerColumns` is named field for field after the row's server columns.
    """
    return dataclasses.replace(row, **dataclasses.asdict(columns))


def _report(warnings: Sequence[str], rows: Sequence[RunRow]) -> ServerJoinReport:
    """Collect the pass's warnings and its counts over the full status vocabulary."""
    status_counts: Counter[str] = Counter(dict.fromkeys(SERVER_STATUSES, 0))
    status_counts.update(row.server_status for row in rows)
    source_counts: Counter[str] = Counter(dict.fromkeys(SERVER_MODEL_SOURCES, 0))
    source_counts.update(row.server_model_source or SOURCE_NONE for row in rows)
    return ServerJoinReport(
        warnings=tuple(dict.fromkeys(warnings)),
        status_counts=status_counts,
        source_counts=source_counts,
    )
