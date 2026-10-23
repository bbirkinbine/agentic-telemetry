"""Daily capture files: `<host dir>/<YYYY-MM-DD>.jsonl.zst`, one JSON record per line.

A file is named by the UTC date each record's call ended (`activity.record_end`:
the activity `timestamp`, else a malformed row's `estimated_end`, else
`harvested_at`). Every
append adds one zstd frame and never rewrites earlier bytes, so readers must
read across frames. A record appended twice (a crash between the append and the
cursor save) is dropped on read by `(activity.id, activity.timestamp)`. A failed
append truncates the file back to its old size; a frame torn anyway (a hard kill,
a copy taken mid-append) is skipped on read with a warning naming the file
(spec 0008 review resolutions).
"""

import json
import os
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path

import zstandard

from agentic_telemetry.captures.activity import record_end

JsonObject = dict[str, object]
SUFFIX = ".jsonl.zst"
# Every zstd frame starts with this magic number, as the zstd library exports it.
ZSTD_MAGIC = zstandard.FRAME_HEADER
READ_CHUNK = 1 << 20


def _activity(record: Mapping[str, object]) -> Mapping[str, object]:
    """Return a record's activity object, or raise naming what is missing."""
    activity = record.get("activity")
    if not isinstance(activity, dict):
        raise ValueError("capture record has no activity object")
    return activity


def daily_name(record: Mapping[str, object]) -> str:
    """Return the file name for a record: its call's UTC end date plus the suffix."""
    _activity(record)
    return record_end(record).date().isoformat() + SUFFIX


def _append_frame(path: Path, frame: bytes) -> None:
    """Append one frame unbuffered and synced; on an `OSError` cut the file back.

    An interrupt or kill mid-write can still leave a torn tail; readers skip it.
    """
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        size = os.fstat(fd).st_size
        try:
            written = 0
            while written < len(frame):
                written += os.write(fd, frame[written:])
            os.fsync(fd)
        except OSError:
            os.ftruncate(fd, size)
            raise
    finally:
        os.close(fd)


def append_records(host_dir: Path, records: Iterable[Mapping[str, object]]) -> list[Path]:
    """Append `records` to their daily files, one synced zstd frame per file.

    Returns:
        The files written, in first-written order.
    """
    batches: dict[str, list[str]] = {}
    for record in records:
        batches.setdefault(daily_name(record), []).append(json.dumps(record) + "\n")
    if not batches:
        return []
    host_dir.mkdir(parents=True, exist_ok=True)
    # A checksum per frame makes a torn or corrupt frame certain to fail decoding.
    compressor = zstandard.ZstdCompressor(write_checksum=True)
    written: list[Path] = []
    for name, lines in batches.items():
        path = host_dir / name
        _append_frame(path, compressor.compress("".join(lines).encode("utf-8")))
        written.append(path)
    return written


def _frame_at(view: memoryview, position: int) -> tuple[bytes | None, int]:
    """Decode the zstd frame starting at `position`, fed in chunks so the pass stays linear.

    Returns:
        `(text, end)` for a whole frame, or `(None, position)` for a torn one.
    """
    decoder = zstandard.ZstdDecompressor().decompressobj()
    parts: list[bytes] = []
    offset = position
    try:
        while offset < len(view) and not decoder.eof:
            chunk = view[offset : offset + READ_CHUNK]
            parts.append(decoder.decompress(chunk))
            offset += len(chunk)
    except zstandard.ZstdError:
        return None, position
    if not decoder.eof:
        return None, position
    return b"".join(parts), offset - len(decoder.unused_data)


def _frames(path: Path, warnings: list[str]) -> list[str]:
    """Return the text of each whole zstd frame in `path`; skip torn ones with a warning."""
    data = path.read_bytes()
    view = memoryview(data)
    texts: list[str] = []
    position = 0
    while position < len(view):
        text, end = _frame_at(view, position)
        if text is not None:
            texts.append(text.decode("utf-8", errors="replace"))
            position = end
            continue
        following = data.find(ZSTD_MAGIC, position + 1)
        warnings.append(f"{path}: skipped an unreadable zstd frame at byte {position}")
        position = following if following != -1 else len(view)
    return texts


def read_records(path: Path, warnings: list[str] | None = None) -> list[JsonObject]:
    """Return every record of one capture file, in file order, duplicates dropped.

    Args:
        path: One daily capture file.
        warnings: Where to note skipped torn frames; None discards the notes.
    """
    text = "".join(_frames(path, warnings if warnings is not None else []))
    seen: set[tuple[object, object]] = set()
    records: list[JsonObject] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as error:
            raise ValueError(f"{path}: line {number} is not JSON") from error
        if not isinstance(record, dict) or not isinstance(record.get("activity"), dict):
            raise ValueError(f"{path}: line {number} is not a capture record")
        activity = _activity(record)
        key = (activity.get("id"), activity.get("timestamp"))
        if key in seen:
            continue
        seen.add(key)
        records.append(record)
    return records


def read_capture_tree(
    root: Path, warnings: list[str] | None = None, days: Collection[str] | None = None
) -> list[JsonObject]:
    """Return the records of the capture files under `root`, host by host, day by day.

    Args:
        root: A captures tree (`<root>/<label>/<YYYY-MM-DD>.jsonl.zst`).
        warnings: Where to note skipped torn frames.
        days: Only the files of these `YYYY-MM-DD` days; None reads every file.
    """
    records: list[JsonObject] = []
    for path in sorted(root.rglob("*" + SUFFIX)):
        if days is None or path.name.removesuffix(SUFFIX) in days:
            records.extend(read_records(path, warnings))
    return records
