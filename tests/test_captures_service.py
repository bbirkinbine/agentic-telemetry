"""The long-running harvester loop (spec 0008: No silent loss, unreachable; Harvester).

Interfaces pinned here:

- `run_forever(poll, interval, sleep, log, max_polls=None) -> None` from
  `captures.service`, called with keywords. It calls `poll()` up to `max_polls`
  times, `sleep(interval)` between polls, and never raises for
  `LlamaSwapUnreachableError` or `LlamaSwapQueryError`: each failed poll logs one
  line naming the API path, never the host, port or key. After 40 quiet polls in a
  row (nothing saved or lost) it logs one line containing `heartbeat`.
- `poll_once` raises the fetch error after writing the rows it fully handled, so
  the cursor stays just before the failed row.
- `LlamaSwapApi(base_url, fetch, api_key)` sends `Authorization: Bearer <key>` on
  every request only when `api_key` is set (llama-swap v264 `auth.go`).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from capture_builders import (
    API_KEY,
    BASE_URL,
    HOST_FRAGMENTS,
    SRC,
    activity_row,
    at,
    capture,
    read_capture_lines,
)
from capture_fake_api import FakeLlamaSwap

from agentic_telemetry.captures.cursor import load_cursor
from agentic_telemetry.captures.harvest import PollReport, poll_once
from agentic_telemetry.captures.llama_swap_api import LlamaSwapApi, LlamaSwapUnreachableError
from agentic_telemetry.captures.service import run_forever

DAY_FILE = "2026-10-22.jsonl.zst"


def poller(fake: FakeLlamaSwap, out: Path, api_key: str | None = None) -> Callable[[], PollReport]:
    """Return a zero-argument poll that reloads the cursor from `out` each time."""
    api = LlamaSwapApi(BASE_URL, fake.fetch, api_key=api_key)
    return lambda: poll_once(api, load_cursor(out / "cursor.json"), out)


def drive(
    poll: Callable[[], PollReport],
    polls: int,
    sleep: Callable[[float], None] | None = None,
) -> tuple[list[str], list[float]]:
    """Run the loop for `polls` polls; return `(log lines, sleep durations)`."""
    logs: list[str] = []
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if sleep is not None:
            sleep(seconds)

    run_forever(poll=poll, interval=15, sleep=fake_sleep, log=logs.append, max_polls=polls)
    return logs, sleeps


def test_unreachable_llama_swap_is_logged_without_host_and_retried(tmp_path: Path) -> None:
    fake = FakeLlamaSwap(unreachable=True)

    logs, sleeps = drive(poller(fake, tmp_path, API_KEY), polls=3)

    assert len(fake.activity_urls()) == 3, "each poll retries"
    assert len(logs) == 3, f"expected one line per failed poll, got {logs}"
    assert all("/api/metrics/activity" in line for line in logs), logs
    leaked = [line for line in logs for text in (*HOST_FRAGMENTS, API_KEY) if text in line]
    assert not leaked, f"host, port or key in log lines: {leaked}"
    assert len(sleeps) >= 2
    assert set(sleeps) == {15}


def test_server_error_is_logged_and_the_next_poll_saves_rows(tmp_path: Path) -> None:
    fake = FakeLlamaSwap(server_error=True)
    fake.add(activity_row(1, at(10)), capture(1))

    def recover(_seconds: float) -> None:
        fake.server_error = False

    logs, _ = drive(poller(fake, tmp_path), polls=2, sleep=recover)

    assert len(logs) == 2
    assert "503" in logs[0] and "/api/metrics/activity" in logs[0]
    assert logs[1] == "poll: rows=1 saved=1 evicted=0 no_capture=0 id_reset=0"
    assert [line["activity"]["id"] for line in read_capture_lines(tmp_path / DAY_FILE)] == [1]  # type: ignore[index]


def test_mid_poll_failure_leaves_the_cursor_before_the_failed_row(tmp_path: Path) -> None:
    fake = FakeLlamaSwap(unreachable_capture_ids={3})
    for row_id in range(1, 5):
        fake.add(activity_row(row_id, at(10, 0, row_id)), capture(row_id))

    with pytest.raises(LlamaSwapUnreachableError):
        poller(fake, tmp_path)()

    assert load_cursor(tmp_path / "cursor.json").last_id == 2
    ids = [line["activity"]["id"] for line in read_capture_lines(tmp_path / DAY_FILE)]  # type: ignore[index]
    assert ids == [1, 2]
    fake.unreachable_capture_ids.clear()
    poller(fake, tmp_path)()
    ids = [line["activity"]["id"] for line in read_capture_lines(tmp_path / DAY_FILE)]  # type: ignore[index]
    assert ids == [1, 2, 3, 4]


def test_mid_poll_failure_inside_the_loop_never_raises(tmp_path: Path) -> None:
    fake = FakeLlamaSwap(unreachable_capture_ids={2})
    fake.add(activity_row(1, at(10, 0, 1)), capture(1))
    fake.add(activity_row(2, at(10, 0, 2)), capture(2))

    logs, _ = drive(poller(fake, tmp_path), polls=2)

    assert len(logs) == 2
    assert all("/api/captures/2" in line for line in logs), logs
    assert not [line for line in logs for text in HOST_FRAGMENTS if text in line]


def test_api_key_is_sent_as_a_bearer_header_on_every_request(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, at(10)), capture(1))

    poller(fake, tmp_path, API_KEY)()

    assert len(fake.calls) >= 2, "expected an activity page and a capture fetch"
    for url, headers in fake.calls:
        values = {key.lower(): value for key, value in headers.items()}
        assert values.get("authorization") == f"Bearer {API_KEY}", url


def test_no_auth_header_is_sent_without_a_key(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, at(10)), capture(1))

    poller(fake, tmp_path, None)()

    for url, headers in fake.calls:
        lowered = {key.lower() for key in headers}
        assert not lowered & {"authorization", "x-api-key"}, url


def test_api_key_never_appears_in_a_log_line(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, at(10)), capture(1))
    fake.add(activity_row(2, at(10, 0, 1)), capture(2))
    fake.unreachable_capture_ids = {2}

    logs, _ = drive(poller(fake, tmp_path, API_KEY), polls=2)

    assert logs
    assert not [line for line in logs if API_KEY in line]


def test_forty_quiet_polls_in_a_row_log_one_heartbeat(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, at(10)), capture(1))

    logs, _ = drive(poller(fake, tmp_path), polls=41)

    assert logs[0].startswith("poll: rows=1 ")
    heartbeats = [line for line in logs[1:] if "heartbeat" in line]
    assert len(heartbeats) == 1 and len(logs) == 2, logs
    assert not [text for text in (*HOST_FRAGMENTS, SRC) if text in heartbeats[0]]


def test_thirty_nine_quiet_polls_log_nothing(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, at(10)), capture(1))

    logs, _ = drive(poller(fake, tmp_path), polls=40)

    assert len(logs) == 1, logs
