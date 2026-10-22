"""The harvester's position in llama-swap's activity list.

A row is new when its `id` is above `last_id`. `last_ts` is the latest call end
saved so far. With llama-swap's `store.path`, ids survive a restart, so the
id-reset rule is a safety net: a listed row at or below `last_id` that ended
after `last_ts` means the id sequence started over (the store was wiped, moved,
or ran in memory), and the harvester opens a new epoch from id 0.
"""

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agentic_telemetry.captures.activity import ActivityRow


class CursorFileError(ValueError):
    """The cursor file exists but cannot be read as a cursor."""


@dataclass(frozen=True)
class Cursor:
    """Where the last poll stopped: id sequence `epoch`, newest id and latest end."""

    epoch: int
    last_id: int
    last_ts: datetime | None


START = Cursor(epoch=0, last_id=0, last_ts=None)


def load_cursor(path: Path) -> Cursor:
    """Return the cursor saved at `path`, or the start of epoch 0 when there is none.

    Raises:
        CursorFileError: The file is not a cursor written by `save_cursor`.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return START
    try:
        data = json.loads(text)
        stamp = data["last_ts"]
        return Cursor(
            epoch=int(data["epoch"]),
            last_id=int(data["last_id"]),
            last_ts=datetime.fromisoformat(stamp) if stamp is not None else None,
        )
    except (ValueError, KeyError, TypeError) as error:
        raise CursorFileError(f"{path}: not a capture cursor ({error})") from error


def save_cursor(path: Path, cursor: Cursor) -> None:
    """Write `cursor` to `path` atomically: a synced temp file, then a rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "epoch": cursor.epoch,
        "last_id": cursor.last_id,
        "last_ts": cursor.last_ts.isoformat() if cursor.last_ts is not None else None,
    }
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


def detect_id_reset(rows: Iterable[ActivityRow], cursor: Cursor) -> bool:
    """True when a row at or below the cursor's id ended after its latest timestamp."""
    if cursor.last_ts is None or cursor.last_id == 0:
        return False
    last_ts = cursor.last_ts
    return any(
        row.id <= cursor.last_id and row.timestamp > last_ts and not row.estimated for row in rows
    )


def new_rows(rows: Iterable[ActivityRow], cursor: Cursor) -> list[ActivityRow]:
    """Return the rows above the cursor's id, oldest id first."""
    return sorted((row for row in rows if row.id > cursor.last_id), key=lambda row: row.id)


def advance(cursor: Cursor, row: ActivityRow) -> Cursor:
    """Return the cursor after saving `row`; an estimated row moves only the id."""
    if row.estimated:
        return Cursor(
            epoch=cursor.epoch, last_id=max(cursor.last_id, row.id), last_ts=cursor.last_ts
        )
    latest = row.timestamp if cursor.last_ts is None else max(cursor.last_ts, row.timestamp)
    return Cursor(epoch=cursor.epoch, last_id=max(cursor.last_id, row.id), last_ts=latest)
