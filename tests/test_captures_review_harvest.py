"""Harvester and file fixes from the spec 0008 reviews (`### Review resolutions`).

The insert race (a recent clean row listed without a capture is fetched anyway),
commits every 100 rows, a stuck capture passed after 3 polls, a missing
`total_pages`, truncation of a failed append, and torn frames skipped on read.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import zstandard
from capture_builders import BASE_URL, activity_row, at, capture, record
from capture_fake_api import FakeLlamaSwap

from agentic_telemetry.captures import harvest
from agentic_telemetry.captures.capture_file import append_records, read_records
from agentic_telemetry.captures.cursor import START, load_cursor
from agentic_telemetry.captures.harvest import poll_once
from agentic_telemetry.captures.llama_swap_api import LlamaSwapApi, LlamaSwapQueryError
from agentic_telemetry.captures.service import report_line

DAY_FILE = "2026-10-22.jsonl.zst"


def stamp(seconds: int) -> datetime:
    """Return 10:00 UTC plus `seconds`."""
    return at(10) + timedelta(seconds=seconds)


def test_recent_clean_row_listed_without_capture_is_fetched_and_saved(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, stamp(1), has_capture=False), capture(1))  # stored a moment late

    report = poll_once(LlamaSwapApi(BASE_URL, fake.fetch), START, tmp_path)

    assert (report.saved, report.no_capture) == (1, 0)
    assert fake.capture_ids_fetched() == [1]


def test_old_or_failed_rows_without_capture_are_not_fetched(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, stamp(0), has_capture=False))  # 120 s before the newest row
    fake.add(activity_row(2, stamp(119), has_capture=False, status=500))
    fake.add(activity_row(3, stamp(120)), capture(3))

    report = poll_once(LlamaSwapApi(BASE_URL, fake.fetch), START, tmp_path)

    assert fake.capture_ids_fetched() == [3]
    assert (report.saved, report.no_capture) == (1, 2)


def test_a_poll_commits_every_hundred_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLlamaSwap()
    for row_id in range(1, 251):
        fake.add(activity_row(row_id, stamp(row_id)), capture(row_id))
    batches: list[int] = []

    def spy(host_dir: Path, records: list[dict[str, object]]) -> list[Path]:
        batches.append(len(records))
        return append_records(host_dir, records)

    monkeypatch.setattr(harvest, "append_records", spy)
    report = poll_once(LlamaSwapApi(BASE_URL, fake.fetch), START, tmp_path)

    assert report.rows == 250
    assert [size for size in batches if size] == [100, 100, 50]
    assert len(read_records(tmp_path / DAY_FILE)) == 250


def failing_capture(fake: FakeLlamaSwap, bad_id: int) -> object:
    """Return a fetch that answers 500 for one capture and defers to `fake` otherwise."""

    def fetch(url: str, headers: Mapping[str, str]) -> bytes:
        if url.endswith(f"/api/captures/{bad_id}"):
            raise LlamaSwapQueryError(f"/api/captures/{bad_id}", 500, "cannot decode")
        return fake.fetch(url, headers)

    return fetch


def test_a_capture_failing_three_polls_is_saved_as_error_and_passed(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    for row_id in (1, 2, 3):
        fake.add(activity_row(row_id, stamp(row_id)), capture(row_id))
    api = LlamaSwapApi(BASE_URL, failing_capture(fake, 2))  # type: ignore[arg-type]
    failures: dict[tuple[str, int], int] = {}

    for _ in range(2):
        with pytest.raises(LlamaSwapQueryError):
            poll_once(api, load_cursor(tmp_path / "cursor.json"), tmp_path, failures)
    report = poll_once(api, load_cursor(tmp_path / "cursor.json"), tmp_path, failures)

    statuses = [line["capture_status"] for line in read_records(tmp_path / DAY_FILE)]
    assert statuses == ["saved", "error", "saved"]
    assert report.errors == 1
    assert report_line(report).endswith(" error=1")


def test_without_failure_memory_a_bad_capture_is_retried_every_poll(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, stamp(1)), capture(1))
    api = LlamaSwapApi(BASE_URL, failing_capture(fake, 1))  # type: ignore[arg-type]

    for _ in range(4):
        with pytest.raises(LlamaSwapQueryError):
            poll_once(api, load_cursor(tmp_path / "cursor.json"), tmp_path)


def test_an_activity_page_without_total_pages_stops_the_poll(tmp_path: Path) -> None:
    def fetch(url: str, headers: Mapping[str, str]) -> bytes:
        return b'{"data": [], "page": 1, "limit": 200, "total": 0}'

    with pytest.raises(LlamaSwapQueryError, match="total_pages"):
        poll_once(LlamaSwapApi(BASE_URL, fetch), START, tmp_path)


def make(row_id: int) -> dict[str, object]:
    """Return a saved record ending on 2026-10-22."""
    return record(activity_row(row_id, stamp(row_id)), capture(row_id))


def test_a_failed_append_is_truncated_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    append_records(tmp_path, [make(1)])
    size = (tmp_path / DAY_FILE).stat().st_size

    def broken_fsync(_fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", broken_fsync)
    with pytest.raises(OSError, match="disk full"):
        append_records(tmp_path, [make(2)])

    assert (tmp_path / DAY_FILE).stat().st_size == size
    assert [line["activity"]["id"] for line in read_records(tmp_path / DAY_FILE)] == [1]  # type: ignore[index]


def test_a_torn_frame_is_skipped_with_a_warning_and_later_frames_read(tmp_path: Path) -> None:
    path = tmp_path / DAY_FILE
    append_records(tmp_path, [make(1)])
    torn = zstandard.ZstdCompressor().compress(b'{"activity": {"id": 99}}\n' * 50)
    with path.open("ab") as handle:
        handle.write(torn[: len(torn) // 2])
    append_records(tmp_path, [make(2)])
    warnings: list[str] = []

    records = read_records(path, warnings)

    assert [line["activity"]["id"] for line in records] == [1, 2]  # type: ignore[index]
    assert len(warnings) == 1 and str(path) in warnings[0]
