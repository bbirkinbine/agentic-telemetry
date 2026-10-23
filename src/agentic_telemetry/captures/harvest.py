"""One harvester poll: list new activity rows, fetch their captures, append, advance.

Pages are read newest id first until one reaches the cursor, so the walk also
sees the rows the id-reset rule needs. A row inserted while paging shifts later
pages by one; that only repeats a row, which is dropped by id. Records are
written in chunks of `COMMIT_ROWS`, each before the cursor is saved, so a crash
between the two repeats rows (dropped on read) and never skips one, and an
outage backlog is never held in memory whole (spec 0008 review resolutions).
"""

from collections.abc import MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentic_telemetry.captures.activity import ActivityRow, parse_activity_row
from agentic_telemetry.captures.capture_file import append_records
from agentic_telemetry.captures.cursor import (
    Cursor,
    advance,
    detect_id_reset,
    new_rows,
    save_cursor,
)
from agentic_telemetry.captures.llama_swap_api import (
    ACTIVITY_PATH,
    CaptureNotFoundError,
    JsonObject,
    LlamaSwapApi,
    LlamaSwapQueryError,
    LlamaSwapUnreachableError,
)

CURSOR_FILE = "cursor.json"
STATUS_SAVED = "saved"
STATUS_EVICTED = "evicted"
STATUS_NONE = "none"
STATUS_ERROR = "error"
COMMIT_ROWS = 100
# A capture failing this many polls in a row (not a 404, not an outage), or an
# activity row that will not parse, is saved with status `error` and passed.
STUCK_POLLS = 3
# Failure memory kept by the caller across polls: ("capture" | "row", call id) -> polls.
Failures = MutableMapping[tuple[str, int], int]
# llama-swap v264 inserts the activity row before it stores the capture, so a
# recent row listed without one may only be early: it is fetched anyway.
RACE_WINDOW = timedelta(seconds=60)


@dataclass(frozen=True)
class PollReport:
    """What one poll did: records written, by capture status, and the new cursor."""

    rows: int
    saved: int
    evicted: int
    no_capture: int
    id_reset: bool
    cursor: Cursor
    errors: int = 0


def _parse_or_pass(item: object, failures: Failures, last_id: int) -> ActivityRow:
    """Parse one served row; after `STUCK_POLLS` polls of failing, pass it as estimated.

    A row at or below `last_id` was handled by an earlier poll (perhaps by an
    earlier process), so if it will not parse it is returned as estimated at
    once: it is listed only for the id-reset check, which ignores estimated rows.

    Raises:
        LlamaSwapQueryError: The row will not parse and has not failed long enough,
            or it has no integer id to remember it by.
    """
    try:
        return parse_activity_row(item)
    except ValueError as error:
        row_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(item, dict) or not isinstance(row_id, int) or isinstance(row_id, bool):
            raise LlamaSwapQueryError(ACTIVITY_PATH, 200, str(error)) from error
        key = ("row", row_id)
        failures[key] = failures.get(key, 0) + (1 if row_id > last_id else STUCK_POLLS)
        if failures[key] < STUCK_POLLS:
            raise LlamaSwapQueryError(ACTIVITY_PATH, 200, f"row {row_id}: {error}") from error
        # The timestamp is filled from its neighbours once the walk is done.
        return ActivityRow(row_id, datetime.min.replace(tzinfo=UTC), False, item, estimated=True)


def _estimate(rows: dict[int, ActivityRow]) -> list[ActivityRow]:
    """Give each estimated row the end of its nearest parsed neighbour by id."""
    parsed = sorted((row for row in rows.values() if not row.estimated), key=lambda r: r.id)
    result: list[ActivityRow] = []
    for row in rows.values():
        if row.estimated:
            nearest = min(parsed, key=lambda r: abs(r.id - row.id), default=None)
            stamp = nearest.timestamp if nearest is not None else datetime.now(UTC)
            row = ActivityRow(row.id, stamp, False, row.raw, estimated=True)
        result.append(row)
    return result


def _walk(api: LlamaSwapApi, last_id: int, failures: Failures) -> list[ActivityRow]:
    """Return the listed rows down to the page that reaches `last_id`, one per id."""
    rows: dict[int, ActivityRow] = {}
    page = 1
    while True:
        body = api.activity_page(page)
        data = body.get("data")
        total_pages = body.get("total_pages")
        if not isinstance(data, list):
            raise LlamaSwapQueryError(ACTIVITY_PATH, 200, "activity page has no data list")
        if not isinstance(total_pages, int):
            raise LlamaSwapQueryError(ACTIVITY_PATH, 200, "activity page has no total_pages")
        parsed = [_parse_or_pass(item, failures, last_id) for item in data]
        for row in parsed:
            rows.setdefault(row.id, row)
        if not parsed or page >= total_pages or any(row.id <= last_id for row in parsed):
            return _estimate(rows)
        page += 1


def _walk_after_reset(api: LlamaSwapApi, failures: Failures, epoch: int) -> list[ActivityRow]:
    """Walk the new id sequence; a failure names the reset, which no later poll reports."""
    try:
        return _walk(api, 0, failures)
    except LlamaSwapQueryError as error:
        detail = f"id reset detected (epoch {epoch}); {error.detail}"
        raise LlamaSwapQueryError(error.path, error.status, detail) from error
    except LlamaSwapUnreachableError as error:
        raise LlamaSwapUnreachableError(f"id reset detected (epoch {epoch}); {error}") from error


def _worth_fetching(row: ActivityRow, newest: datetime) -> bool:
    """True when a row may have a capture: listed with one, or a recent clean success."""
    if row.has_capture:
        return True
    raw = row.raw
    clean = raw.get("resp_status_code") == 200 and not raw.get("error_msg")
    return clean and newest - row.timestamp <= RACE_WINDOW


def _capture(
    api: LlamaSwapApi, row: ActivityRow, failures: Failures
) -> tuple[JsonObject | None, str]:
    """Fetch one row's capture; return it with its status, or raise to retry later."""
    key = ("capture", row.id)
    try:
        captured = api.capture(row.id)
    except CaptureNotFoundError:
        failures.pop(key, None)
        return None, STATUS_EVICTED if row.has_capture else STATUS_NONE
    except LlamaSwapUnreachableError:
        raise
    except LlamaSwapQueryError:
        failures[key] = failures.get(key, 0) + 1
        if failures[key] < STUCK_POLLS:
            raise
        failures.pop(key, None)
        return None, STATUS_ERROR
    failures.pop(key, None)
    return captured, STATUS_SAVED


def _record(row: ActivityRow, captured: JsonObject | None, status: str, epoch: int) -> JsonObject:
    """Return one harvested record in the spec's shape.

    A malformed row passed as `error` also carries `estimated_end`, its
    neighbours' call end, so it can be filed by day and listed in order.
    """
    record: JsonObject = {
        "activity": row.raw,
        "capture": captured,
        "capture_status": status,
        "harvested_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "epoch": epoch,
    }
    if row.estimated:
        record["estimated_end"] = row.timestamp.isoformat()
    return record


class _Writer:
    """Appends records in chunks and saves the cursor after each chunk."""

    def __init__(self, out: Path, start: Cursor) -> None:
        self.out = out
        self.saved_cursor = start
        self.pending: list[JsonObject] = []

    def add(self, record: JsonObject, cursor: Cursor) -> None:
        self.pending.append(record)
        if len(self.pending) >= COMMIT_ROWS:
            self.commit(cursor)

    def commit(self, cursor: Cursor) -> None:
        append_records(self.out, self.pending)
        self.pending = []
        if cursor != self.saved_cursor:
            save_cursor(self.out / CURSOR_FILE, cursor)
            self.saved_cursor = cursor


def poll_once(
    api: LlamaSwapApi,
    cursor: Cursor,
    out: Path,
    failures: Failures | None = None,
) -> PollReport:
    """Save every call llama-swap listed since `cursor` into `out`.

    Args:
        api: The llama-swap API.
        cursor: Where the previous poll stopped.
        out: The host directory: daily files and `cursor.json` go here.
        failures: Consecutive failures by `("capture" | "row", call id)`, kept by
            the caller across polls; a capture fetch or row parse failing
            `STUCK_POLLS` times is saved with status `error`. None keeps no
            memory (each poll retries).

    Returns:
        Counts by capture status and the new cursor.

    Raises:
        LlamaSwapUnreachableError, LlamaSwapQueryError: A request failed. Rows
            handled before the failure are written and the cursor saved just
            before the failed row, so the next poll resumes there.
    """
    failures = failures if failures is not None else {}
    writer = _Writer(out, cursor)
    listed = _walk(api, cursor.last_id, failures)
    id_reset = detect_id_reset(listed, cursor)
    if id_reset:
        cursor = Cursor(epoch=cursor.epoch + 1, last_id=0, last_ts=None)
        failures.clear()  # old-epoch ids mean nothing in the new sequence
        # Saved at once: if the new sequence's walk fails (say, a malformed row
        # not yet passed), the next poll walks the new epoch instead of
        # detecting the same reset again and never getting past it.
        save_cursor(out / CURSOR_FILE, cursor)
        writer.saved_cursor = cursor
        listed = _walk_after_reset(api, failures, cursor.epoch)
    rows = new_rows(listed, cursor)
    newest = max((row.timestamp for row in listed if not row.estimated), default=datetime.now(UTC))
    counts = dict.fromkeys((STATUS_SAVED, STATUS_EVICTED, STATUS_NONE, STATUS_ERROR), 0)
    try:
        for row in rows:
            captured, status = None, STATUS_ERROR if row.estimated else STATUS_NONE
            if not row.estimated and _worth_fetching(row, newest):
                captured, status = _capture(api, row, failures)
            counts[status] += 1
            cursor = advance(cursor, row)
            writer.add(_record(row, captured, status, cursor.epoch), cursor)
    finally:
        writer.commit(cursor)
    return PollReport(
        rows=sum(counts.values()),
        saved=counts[STATUS_SAVED],
        evicted=counts[STATUS_EVICTED],
        no_capture=counts[STATUS_NONE],
        id_reset=id_reset,
        cursor=cursor,
        errors=counts[STATUS_ERROR],
    )
