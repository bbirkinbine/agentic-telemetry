"""One harvester poll against a fake llama-swap (spec 0008: Harvester, No silent loss counts).

Interfaces pinned here (all data in, data out; no network):

- `LlamaSwapApi(base_url, fetch, api_key=None)` from `captures.llama_swap_api`.
- `Cursor(epoch, last_id, last_ts)` and `load_cursor(path) -> Cursor` from
  `captures.cursor`; a missing file loads as `Cursor(epoch=0, last_id=0, last_ts=None)`.
  `last_ts` is an aware UTC datetime (nanoseconds trimmed to microseconds).
- `poll_once(api, cursor, out) -> PollReport` from `captures.harvest`. `out` is the
  host directory `captures/<host>/`: records go to `out/<UTC date>.jsonl.zst` and the
  cursor to `out/cursor.json`. `PollReport` has `rows` (records written),
  `saved`, `evicted`, `no_capture`, `id_reset` (bool) and `cursor` (the new cursor).
- `run_forever(poll=, interval=, sleep=, log=, max_polls=)` from `captures.service`
  logs `poll: rows=N saved=S evicted=E no_capture=K id_reset=0|1` for a poll that
  saved or lost anything.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from capture_builders import (
    BASE_URL,
    HOST_FRAGMENTS,
    SENTINEL,
    SRC,
    activity_row,
    at,
    capture,
    read_capture_lines,
)
from capture_fake_api import FakeLlamaSwap

from agentic_telemetry.captures.cursor import Cursor, load_cursor
from agentic_telemetry.captures.harvest import PollReport, poll_once
from agentic_telemetry.captures.llama_swap_api import LlamaSwapApi
from agentic_telemetry.captures.service import run_forever

DAY_FILE = "2026-10-22.jsonl.zst"


def stamp(row_id: int) -> datetime:
    """Return row `row_id`'s end time: one second apart from 10:00 UTC."""
    return at(10) + timedelta(seconds=row_id)


def bulk(fake: FakeLlamaSwap, first: int, last: int) -> None:
    """Add rows `first..last` with no capture."""
    for row_id in range(first, last + 1):
        fake.add(activity_row(row_id, stamp(row_id), has_capture=False))


def poll(fake: FakeLlamaSwap, out: Path, cursor: Cursor | None = None) -> PollReport:
    """Run one poll, loading the cursor from `out` unless one is given."""
    api = LlamaSwapApi(BASE_URL, fake.fetch)
    current = cursor if cursor is not None else load_cursor(out / "cursor.json")
    return poll_once(api, current, out)


def saved_ids(out: Path) -> list[int]:
    """Return every record's activity id in file order, duplicates kept."""
    return [
        int(str(line["activity"]["id"]))  # type: ignore[index]
        for path in sorted(out.glob("*.jsonl.zst"))
        for line in read_capture_lines(path)
    ]


def test_new_rows_are_saved_in_ascending_id_order(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    for row_id in (2, 3, 1):
        fake.add(activity_row(row_id, stamp(row_id)), capture(row_id))

    report = poll(fake, tmp_path)

    assert saved_ids(tmp_path) == [1, 2, 3]
    assert (report.rows, report.saved) == (3, 3)


def test_activity_pages_are_requested_by_id_descending_two_hundred_at_a_time(
    tmp_path: Path,
) -> None:
    fake = FakeLlamaSwap()
    bulk(fake, 1, 3)

    poll(fake, tmp_path)

    urls = fake.activity_urls()
    assert urls, "expected at least one GET /api/metrics/activity"
    query = parse_qs(urlsplit(urls[0]).query)
    assert query["sort"] == ["id"]
    assert query["order"] == ["desc"]
    assert query["limit"] == ["200"]
    assert query["page"] == ["1"]


def test_descending_walk_stops_at_the_page_that_reaches_the_cursor(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    bulk(fake, 1, 450)
    cursor = Cursor(epoch=0, last_id=200, last_ts=stamp(200))

    report = poll(fake, tmp_path, cursor)

    pages = [parse_qs(urlsplit(url).query)["page"] for url in fake.activity_urls()]
    assert pages == [["1"], ["2"]], "page 2 holds id 200, so page 3 is never read"
    assert saved_ids(tmp_path) == list(range(201, 451))
    assert report.rows == 250


def test_row_inserted_mid_walk_is_neither_duplicated_nor_skipped(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    bulk(fake, 1, 450)
    fake.insert_after_page = {1: [activity_row(451, stamp(451), has_capture=False)]}
    cursor = Cursor(epoch=0, last_id=100, last_ts=stamp(100))

    poll(fake, tmp_path, cursor)
    first = saved_ids(tmp_path)
    poll(fake, tmp_path)

    assert first == list(range(101, 451)), "page 2 repeats id 251 after the insert"
    assert saved_ids(tmp_path) == list(range(101, 452))


def test_first_run_without_cursor_backfills_every_page_with_null_captures(
    tmp_path: Path,
) -> None:
    fake = FakeLlamaSwap()
    bulk(fake, 1, 450)

    report = poll(fake, tmp_path)

    assert len(fake.activity_urls()) == 3
    assert saved_ids(tmp_path) == list(range(1, 451))
    lines = read_capture_lines(tmp_path / DAY_FILE)
    assert all(line["capture"] is None for line in lines)
    assert {line["capture_status"] for line in lines} == {"none"}
    assert (report.rows, report.saved, report.no_capture) == (450, 0, 450)


def test_records_hold_activity_and_capture_exactly_as_served(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    row = activity_row(7, "2026-10-22T12:00:01.123456789+02:00")
    row["future_field"] = {"kept": [1, 2]}
    served = capture(7, response=b'data: {"x": 1}\n\ndata: [DONE]\n\n')
    fake.add(row, served)

    poll(fake, tmp_path)

    [line] = read_capture_lines(tmp_path / DAY_FILE)
    assert line["activity"] == row
    assert line["capture"] == served
    assert line["capture_status"] == "saved"
    assert line["epoch"] == 0
    assert isinstance(line["harvested_at"], str)
    datetime.fromisoformat(str(line["harvested_at"]))


def test_cursor_advances_to_newest_id_and_latest_timestamp(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, "2026-10-22T10:00:03.123456789Z", has_capture=False))
    fake.add(activity_row(2, "2026-10-22T10:00:02Z", has_capture=False))

    report = poll(fake, tmp_path)

    expected = Cursor(epoch=0, last_id=2, last_ts=datetime(2026, 10, 22, 10, 0, 3, 123456, UTC))
    assert report.cursor == expected
    assert load_cursor(tmp_path / "cursor.json") == expected


def test_quiet_poll_writes_nothing(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, stamp(1)), capture(1))
    poll(fake, tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.glob("*.jsonl.zst")}

    report = poll(fake, tmp_path)

    after = {path.name: path.read_bytes() for path in tmp_path.glob("*.jsonl.zst")}
    assert after == before
    assert (report.rows, report.saved, report.evicted, report.no_capture) == (0, 0, 0, 0)
    assert fake.capture_ids_fetched() == [1], "a quiet poll fetches no capture"


def test_capture_answering_404_counts_as_evicted_and_the_row_is_saved(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, stamp(1), has_capture=True))

    report = poll(fake, tmp_path)

    assert (report.rows, report.saved, report.evicted, report.no_capture) == (1, 0, 1, 0)
    [line] = read_capture_lines(tmp_path / DAY_FILE)
    assert line["capture"] is None
    assert line["capture_status"] == "evicted"


def test_row_without_capture_counts_as_no_capture_when_the_fetch_finds_none(
    tmp_path: Path,
) -> None:
    """Review resolution: a recent clean row is fetched anyway (insert race); 404 = none."""
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, stamp(1), has_capture=False))

    report = poll(fake, tmp_path)

    assert (report.rows, report.saved, report.evicted, report.no_capture) == (1, 0, 0, 1)
    assert fake.capture_ids_fetched() == [1]
    [line] = read_capture_lines(tmp_path / DAY_FILE)
    assert line["capture_status"] == "none"


def test_report_line_has_exact_counts_and_no_host_content_or_src(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, stamp(1)), capture(1))
    fake.add(activity_row(2, stamp(2), has_capture=True))
    fake.add(activity_row(3, stamp(3), has_capture=False))
    logs: list[str] = []

    run_forever(
        poll=lambda: poll(fake, tmp_path),
        interval=15,
        sleep=lambda _seconds: None,
        log=logs.append,
        max_polls=1,
    )

    assert logs == ["poll: rows=3 saved=1 evicted=1 no_capture=1 id_reset=0"]
    forbidden = (*HOST_FRAGMENTS, SENTINEL, SRC)
    assert not [line for line in logs for text in forbidden if text in line]
