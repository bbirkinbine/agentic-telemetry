"""Each run's own time window, from the records the run itself left behind (spec 0005).

No request carries a run ID through llama-swap, so the join key is model plus
time. A ledger `run` row's `ts` is the run's *end*; its start comes from the
stamp its run ID ends with, and failing that from `ts` minus its recorded
seconds.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# Mirrors `collector/inference-agent/otel-agent.yaml`: its `scrape_interval` and
# its `job_name`, which the Collector's Prometheus receiver stores as the
# `service_name` label. tests/test_server_window.py checks both against that
# file, so a change there cannot drift past these two constants unnoticed.
SCRAPE_INTERVAL_SECONDS = 30
SERVICE_NAME = "inference-models"

_STAMP_AT_END = re.compile(r"-(\d{8}T\d{6}Z)-\d+$")


@dataclass(frozen=True)
class RunWindow:
    """One run's start and end, both aware, from its own records."""

    start: datetime
    end: datetime


def start_stamp(run_id: str) -> datetime | None:
    """Return the UTC start time a run ID ends with, or None when it carries none.

    The delegate runner names a run `<purpose>-<YYYYMMDDTHHMMSS>Z-<pid>`, so the
    stamp is only a start when the PID follows it at the very end.
    """
    match = _STAMP_AT_END.search(run_id)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def run_window(
    run_id: str,
    ts: str | None,
    wall_seconds: float | None,
    agent_seconds: float | None,
) -> RunWindow | None:
    """Return the window one run records for itself, or None when it records none.

    The end is the ledger's `ts` (a run's end time). The start is the run ID's
    stamp when it has one, else `ts` minus `wall_seconds`, else `ts` minus the
    summed `agent_seconds`.

    Args:
        run_id: The run's ID, which may end in a start stamp and PID.
        ts: The ledger row's `ts`, the run's end; None when absent or unparsable.
        wall_seconds: The run's wall-clock duration, if recorded.
        agent_seconds: The run's agent-time duration, the fallback.

    Returns:
        The window, or None with no end, no way to reach a start, or a start
        after the end.
    """
    end = _timestamp(ts)
    if end is None:
        return None
    start = start_stamp(run_id)
    if start is None:
        seconds = wall_seconds if wall_seconds is not None else agent_seconds
        if seconds is None:
            return None
        start = end - timedelta(seconds=seconds)
    if start > end:
        return None
    return RunWindow(start=start, end=end)


def widen(window: RunWindow, seconds: float) -> RunWindow:
    """Return `window` with each edge moved out by `seconds`; leaves `window` alone.

    The join uses only the widened end, to tell when a run is too recent for
    VictoriaMetrics to have published its last samples. The queries themselves
    take no start-side slack (spec 0005, D8).
    """
    pad = timedelta(seconds=seconds)
    return RunWindow(start=window.start - pad, end=window.end + pad)


def overlaps(first: RunWindow, second: RunWindow) -> bool:
    """True when the two windows share an instant; back-to-back runs do not overlap."""
    return first.start < second.end and second.start < first.end


def _timestamp(value: str | None) -> datetime | None:
    """Parse a ledger timestamp as aware UTC; a naive time is taken as UTC."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
