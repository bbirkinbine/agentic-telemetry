"""Daily capture files (spec 0008: Harvester; plan "Records").

Interfaces pinned here, from `agentic_telemetry.captures.capture_file`:

- `append_records(host_dir, records) -> None | list[Path]`: appends each record as
  one JSON line to `host_dir/<UTC date of activity.timestamp>.jsonl.zst`, one zstd
  frame per file per call, creating `host_dir` when missing. Existing bytes are
  never rewritten.
- `read_records(path) -> list[dict]`: every record of one file, read across
  frames, in file order, with duplicates by `(activity.id, activity.timestamp)`
  dropped (the first kept).
"""

from __future__ import annotations

import json
from pathlib import Path

import zstandard
from capture_builders import activity_row, at, capture, read_capture_lines, record

from agentic_telemetry.captures.capture_file import append_records, read_records


def make(row_id: int, timestamp: str) -> dict[str, object]:
    """Return a saved record for row `row_id` ending at `timestamp` (RFC3339Nano text)."""
    return record(activity_row(row_id, timestamp), capture(row_id))


def test_file_is_named_by_the_utc_date_of_the_activity_timestamp(tmp_path: Path) -> None:
    host_dir = tmp_path / "captures" / "gpu"
    late_local = make(1, "2026-10-22T20:30:00.123456789-05:00")  # 01:30 UTC on the 23rd

    append_records(host_dir, [late_local])

    assert sorted(path.name for path in host_dir.iterdir() if path.suffix == ".zst") == [
        "2026-10-23.jsonl.zst"
    ]
    assert read_capture_lines(host_dir / "2026-10-23.jsonl.zst") == [late_local]


def test_batch_spanning_utc_midnight_splits_into_two_daily_files(tmp_path: Path) -> None:
    before = make(1, "2026-10-22T23:59:59.900000001Z")
    after = make(2, "2026-10-23T00:00:00.1Z")

    append_records(tmp_path, [before, after])

    assert read_capture_lines(tmp_path / "2026-10-22.jsonl.zst") == [before]
    assert read_capture_lines(tmp_path / "2026-10-23.jsonl.zst") == [after]


def test_records_keep_their_order_within_a_file(tmp_path: Path) -> None:
    batch = [make(row_id, f"2026-10-22T10:00:0{row_id}Z") for row_id in (1, 2, 3)]

    append_records(tmp_path, batch)

    assert read_records(tmp_path / "2026-10-22.jsonl.zst") == batch


def test_second_batch_appends_a_new_frame_without_rewriting_the_first(tmp_path: Path) -> None:
    first = make(1, "2026-10-22T10:00:01Z")
    second = make(2, "2026-10-22T10:00:02Z")
    path = tmp_path / "2026-10-22.jsonl.zst"

    append_records(tmp_path, [first])
    head = path.read_bytes()
    append_records(tmp_path, [second])
    data = path.read_bytes()

    assert data.startswith(head), "the first frame must be left as written"
    tail = zstandard.ZstdDecompressor().decompressobj().decompress(data[len(head) :])
    assert [json.loads(line) for line in tail.decode("utf-8").splitlines()] == [second], (
        "the second batch is its own zstd frame"
    )
    assert read_records(path) == [first, second]


def test_reader_drops_a_record_written_twice(tmp_path: Path) -> None:
    once = make(1, "2026-10-22T10:00:01.5Z")
    other = make(2, "2026-10-22T10:00:02Z")

    append_records(tmp_path, [once])
    append_records(tmp_path, [once, other])  # a crash before the cursor was saved

    assert read_records(tmp_path / "2026-10-22.jsonl.zst") == [once, other]
    assert len(read_capture_lines(tmp_path / "2026-10-22.jsonl.zst")) == 3


def test_reader_keeps_a_reused_id_with_a_different_timestamp(tmp_path: Path) -> None:
    old_epoch = record(activity_row(1, at(10)), capture(1), epoch=0)
    new_epoch = record(activity_row(1, at(11)), capture(1), epoch=1)

    append_records(tmp_path, [old_epoch, new_epoch])

    assert read_records(tmp_path / "2026-10-22.jsonl.zst") == [old_epoch, new_epoch]


def test_record_without_capture_is_written_with_null_capture(tmp_path: Path) -> None:
    lost = record(activity_row(4, at(12), has_capture=False), None, status="none")

    append_records(tmp_path, [lost])

    [line] = read_records(tmp_path / "2026-10-22.jsonl.zst")
    assert line["capture"] is None
    assert line["capture_status"] == "none"
