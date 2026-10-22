"""llama-swap activity rows, as `GET /api/metrics/activity` serves them.

Field names are those of `ActivityLogEntry` in llama-swap v264 (MIT),
`internal/store/activity.go`, retrieved 2026-10-22 (spec 0008). `timestamp` is
the call's end in Go's RFC 3339: up to nine fraction digits from the in-memory
store, whole seconds in the host's zone from the SQLite store (`store.path`,
which keeps Unix seconds). `duration_ms` is the call's length.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

JsonObject = dict[str, object]

_GO_TIME = re.compile(
    r"^(?P<base>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(?P<frac>\d+))?(?P<zone>Z|[+-]\d\d:\d\d)$"
)


def parse_go_time(text: str) -> datetime:
    """Return a Go RFC3339Nano timestamp as an aware UTC datetime.

    Fractions past microseconds are truncated, since Python keeps six digits.

    Raises:
        ValueError: `text` is not an RFC 3339 timestamp with a zone.
    """
    match = _GO_TIME.match(text)
    if match is None:
        raise ValueError(f"not an RFC 3339 timestamp with a zone: {text!r}")
    fraction = (match["frac"] or "")[:6]
    zone = "+00:00" if match["zone"] == "Z" else match["zone"]
    normalized = match["base"] + (f".{fraction.ljust(6, '0')}" if fraction else "") + zone
    return datetime.fromisoformat(normalized).astimezone(UTC)


@dataclass(frozen=True)
class ActivityRow:
    """The fields the harvester needs from one activity row, plus the row as served."""

    id: int
    timestamp: datetime
    has_capture: bool
    raw: JsonObject
    # True for a malformed row passed after `STUCK_POLLS`: `timestamp` is its
    # neighbours' estimate, so it never moves `last_ts` or triggers an id reset.
    estimated: bool = False


def parse_activity_row(raw: object) -> ActivityRow:
    """Return the typed view of one served activity row.

    Raises:
        ValueError: The row is not an object or lacks a usable `id`,
            `timestamp` or `has_capture`.
    """
    if not isinstance(raw, dict):
        raise ValueError("activity row is not a JSON object")
    row_id = raw.get("id")
    stamp = raw.get("timestamp")
    has_capture = raw.get("has_capture", False)
    if not isinstance(row_id, int) or isinstance(row_id, bool):
        raise ValueError(f"activity row has no integer id: {row_id!r}")
    if not isinstance(stamp, str):
        raise ValueError(f"activity row {row_id} has no timestamp")
    if not isinstance(has_capture, bool):
        raise ValueError(f"activity row {row_id} has a non-boolean has_capture")
    return ActivityRow(id=row_id, timestamp=parse_go_time(stamp), has_capture=has_capture, raw=raw)


def token_count(activity: JsonObject, field: str) -> int | None:
    """Return one of the nested `tokens` counts, or None when absent."""
    tokens = activity.get("tokens")
    if not isinstance(tokens, dict):
        return None
    value = tokens.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def record_end(record: Mapping[str, object]) -> datetime:
    """Return when a harvested record's call ended.

    The activity `timestamp`, else the `estimated_end` the harvester wrote for a
    malformed row, else when it was harvested.

    Raises:
        ValueError: The record carries none of the three.
    """
    activity = record.get("activity")
    stamp = activity.get("timestamp") if isinstance(activity, dict) else None
    for candidate in (stamp, record.get("estimated_end"), record.get("harvested_at")):
        if isinstance(candidate, str):
            try:
                return parse_go_time(candidate)
            except ValueError:
                continue
    raise ValueError("capture record has no usable call end")


def record_start(record: Mapping[str, object]) -> datetime:
    """Return when a harvested record's call started: its end minus `duration_ms`."""
    activity = record.get("activity")
    duration = activity.get("duration_ms") if isinstance(activity, dict) else None
    millis = duration if isinstance(duration, int) and not isinstance(duration, bool) else 0
    return record_end(record) - timedelta(milliseconds=max(millis, 0))
