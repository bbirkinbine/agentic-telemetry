"""Id-reset safety net and cursor persistence (spec 0008: No silent loss, second bullet).

With `store.path`, llama-swap row ids survive a restart, so the rule never fires
in normal operation. It fires when a listed row has `id <= last_id` and a
`timestamp > last_ts` (the store was wiped, moved, or ran in memory): the poll
sets `epoch += 1`, `last_id = 0`, and saves every new row. `last_ts` is the
latest timestamp saved so far, not the timestamp of row `last_id`, because
concurrent calls can finish out of id order.

Interfaces (see `test_captures_poll.py`): `poll_once`, `LlamaSwapApi`, `Cursor`,
plus `save_cursor(path, cursor)` from `captures.cursor`, which writes JSON with
the keys `epoch`, `last_id` and `last_ts` atomically (temp file, then rename).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from capture_builders import BASE_URL, activity_row, at, capture, read_capture_lines
from capture_fake_api import FakeLlamaSwap

from agentic_telemetry.captures.cursor import Cursor, load_cursor, save_cursor
from agentic_telemetry.captures.harvest import PollReport, poll_once
from agentic_telemetry.captures.llama_swap_api import LlamaSwapApi, LlamaSwapQueryError
from agentic_telemetry.captures.service import run_forever

DAY_FILE = "2026-10-22.jsonl.zst"


def stamp(seconds: int) -> datetime:
    """Return 10:00 UTC plus `seconds`."""
    return at(10) + timedelta(seconds=seconds)


def poll(fake: FakeLlamaSwap, out: Path) -> PollReport:
    """Run one poll with a fresh API object and the cursor saved in `out`."""
    return poll_once(LlamaSwapApi(BASE_URL, fake.fetch), load_cursor(out / "cursor.json"), out)


def lines(out: Path) -> list[tuple[int, int]]:
    """Return `(activity id, epoch)` for every record, duplicates kept."""
    return [
        (int(str(line["activity"]["id"])), int(str(line["epoch"])))  # type: ignore[index]
        for line in read_capture_lines(out / DAY_FILE)
    ]


def store_with(first: int, last: int, offset: int = 0) -> FakeLlamaSwap:
    """Return a fake holding rows `first..last` (with captures), row i at `stamp(i + offset)`."""
    fake = FakeLlamaSwap()
    for row_id in range(first, last + 1):
        fake.add(activity_row(row_id, stamp(row_id + offset)), capture(row_id))
    return fake


def test_ids_continuing_after_a_llama_swap_restart_are_not_a_reset(tmp_path: Path) -> None:
    poll(store_with(1, 5), tmp_path)
    restarted = FakeLlamaSwap()
    for row_id in range(1, 7):  # the cache emptied: rows 1-6 kept, captures gone
        restarted.add(activity_row(row_id, stamp(row_id), has_capture=False))
    restarted.add(activity_row(7, stamp(60)), capture(7))

    report = poll(restarted, tmp_path)

    assert report.id_reset is False
    assert (report.rows, report.saved, report.no_capture) == (2, 1, 1)
    assert report.cursor.epoch == 0
    assert report.cursor.last_id == 7
    assert lines(tmp_path) == [(row_id, 0) for row_id in range(1, 8)]


def test_lower_id_with_newer_timestamp_starts_a_new_epoch_and_saves_every_row(
    tmp_path: Path,
) -> None:
    poll(store_with(1, 5), tmp_path)
    wiped = store_with(1, 3, offset=100)

    report = poll(wiped, tmp_path)

    assert report.id_reset is True
    assert report.cursor.epoch == 1
    assert report.cursor.last_id == 3
    assert lines(tmp_path) == [*((i, 0) for i in range(1, 6)), *((i, 1) for i in range(1, 4))]


def test_reset_whose_new_ids_passed_the_old_cursor_is_still_detected(tmp_path: Path) -> None:
    poll(store_with(1, 5), tmp_path)
    wiped = store_with(1, 8, offset=100)

    report = poll(wiped, tmp_path)

    assert report.id_reset is True
    assert report.rows == 8, "ids 1-5 of the new sequence must not be skipped"
    assert [pair for pair in lines(tmp_path) if pair[1] == 1] == [(i, 1) for i in range(1, 9)]


def test_same_id_with_same_timestamp_is_not_a_reset(tmp_path: Path) -> None:
    fake = store_with(1, 5)
    poll(fake, tmp_path)
    fake.add(activity_row(6, stamp(6)), capture(6))

    report = poll(fake, tmp_path)

    assert report.id_reset is False
    assert report.cursor.epoch == 0
    assert lines(tmp_path) == [(i, 0) for i in range(1, 7)]


def test_row_finishing_out_of_id_order_is_not_a_reset(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, stamp(5)), capture(1))
    fake.add(activity_row(2, stamp(4)), capture(2))
    poll(fake, tmp_path)
    fake.add(activity_row(3, stamp(6)), capture(3))

    report = poll(fake, tmp_path)

    assert report.id_reset is False
    assert lines(tmp_path) == [(1, 0), (2, 0), (3, 0)]


def test_reset_is_reported_in_the_poll_line(tmp_path: Path) -> None:
    poll(store_with(1, 5), tmp_path)
    wiped = store_with(1, 2, offset=100)
    logs: list[str] = []

    run_forever(
        poll=lambda: poll(wiped, tmp_path),
        interval=15,
        sleep=lambda _seconds: None,
        log=logs.append,
        max_polls=1,
    )

    assert "poll: rows=2 saved=2 evicted=0 no_capture=0 id_reset=1" in logs


def test_cursor_resumes_after_a_harvester_restart_without_duplicates(tmp_path: Path) -> None:
    fake = store_with(1, 3)
    poll(fake, tmp_path)
    for row_id in (4, 5):
        fake.add(activity_row(row_id, stamp(row_id)), capture(row_id))

    report = poll(fake, tmp_path)  # a new process: API object and cursor rebuilt

    assert report.rows == 2
    assert lines(tmp_path) == [(i, 0) for i in range(1, 6)]
    assert sorted(fake.capture_ids_fetched()) == [1, 2, 3, 4, 5], "no capture is fetched twice"


def test_missing_cursor_file_loads_as_the_start_of_epoch_zero(tmp_path: Path) -> None:
    assert load_cursor(tmp_path / "cursor.json") == Cursor(epoch=0, last_id=0, last_ts=None)


def test_saved_cursor_round_trips_with_only_the_three_plan_keys(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    cursor = Cursor(epoch=2, last_id=41, last_ts=at(10, 5, 6, 789012))

    save_cursor(path, cursor)

    assert load_cursor(path) == cursor
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"epoch", "last_id", "last_ts"}
    assert [entry.name for entry in tmp_path.iterdir()] == ["cursor.json"], "no temp file left"


def test_saving_a_cursor_replaces_the_previous_one(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    save_cursor(path, Cursor(epoch=0, last_id=3, last_ts=at(10)))

    save_cursor(path, Cursor(epoch=0, last_id=9, last_ts=at(11)))

    assert load_cursor(path) == Cursor(epoch=0, last_id=9, last_ts=at(11))


def test_reset_with_a_malformed_new_row_still_reaches_the_new_epoch(tmp_path: Path) -> None:
    """Review fix: the reset cursor is saved before the new sequence is walked."""
    save_cursor(tmp_path / "cursor.json", Cursor(epoch=0, last_id=100, last_ts=stamp(100)))
    wiped = FakeLlamaSwap()
    for row_id in range(1, 6):
        row = activity_row(row_id, stamp(200 + row_id))
        wiped.add({**row, "timestamp": "garbled"} if row_id == 3 else row, capture(row_id))
    api = LlamaSwapApi(BASE_URL, wiped.fetch)
    failures: dict[tuple[str, int], int] = {}

    with pytest.raises(LlamaSwapQueryError, match="id reset detected"):
        poll_once(api, load_cursor(tmp_path / "cursor.json"), tmp_path, failures)
    assert load_cursor(tmp_path / "cursor.json").epoch == 1
    with pytest.raises(LlamaSwapQueryError, match="row 3"):
        poll_once(api, load_cursor(tmp_path / "cursor.json"), tmp_path, failures)
    report = poll_once(api, load_cursor(tmp_path / "cursor.json"), tmp_path, failures)

    records = read_capture_lines(tmp_path / DAY_FILE)
    assert [(line["capture_status"], line["epoch"]) for line in records] == [
        ("saved", 1),
        ("saved", 1),
        ("error", 1),
        ("saved", 1),
        ("saved", 1),
    ]
    assert "estimated_end" in records[2]
    assert (report.rows, report.cursor.epoch, report.cursor.last_id) == (5, 1, 5)
