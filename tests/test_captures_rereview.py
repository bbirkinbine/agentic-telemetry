"""Fixes from the spec 0008 re-review (`### Review resolutions`, second round).

A failed write is cut back; an unreachable capture is retried, never passed;
the cursor is saved per 100-row chunk; a malformed activity row is passed after
3 polls with an estimated end; an empty host label is a usage error; delegate
links need the orchestrator's project and window; non-chat stream events are
shown; and `captures show` reads only the days around its selection.
"""

from __future__ import annotations

import dataclasses
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import zstandard
from capture_builders import (
    BASE_URL,
    QWEN,
    activity_row,
    at,
    capture,
    chunk,
    record,
    session_row,
    sse,
    write_capture_file,
    write_sessions,
)
from capture_cli_helpers import CaptureEvidence, run_cli, show_args
from capture_fake_api import FakeLlamaSwap
from capture_join_helpers import call, claims, owners

from agentic_telemetry.captures.capture_file import append_records, read_records
from agentic_telemetry.captures.cursor import START, load_cursor
from agentic_telemetry.captures.harvest import poll_once
from agentic_telemetry.captures.llama_swap_api import (
    LlamaSwapApi,
    LlamaSwapQueryError,
    LlamaSwapUnreachableError,
)

DAY_FILE = "2026-10-22.jsonl.zst"


def stamp(seconds: int) -> datetime:
    """Return 10:00 UTC plus `seconds`."""
    return at(10) + timedelta(seconds=seconds)


def test_a_failed_write_is_cut_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    append_records(tmp_path, [record(activity_row(1, stamp(1)), capture(1))])
    size = (tmp_path / DAY_FILE).stat().st_size
    real_write = os.write

    def half_then_fail(fd: int, data: bytes) -> int:
        monkeypatch.setattr(os, "write", failing)
        return real_write(fd, data[: len(data) // 2])

    def failing(_fd: int, _data: bytes) -> int:
        raise OSError("no space left on device")

    monkeypatch.setattr(os, "write", half_then_fail)
    with pytest.raises(OSError, match="no space"):
        append_records(tmp_path, [record(activity_row(2, stamp(2)), capture(2))])

    assert (tmp_path / DAY_FILE).stat().st_size == size


def test_an_unreachable_capture_is_retried_never_passed(tmp_path: Path) -> None:
    fake = FakeLlamaSwap(unreachable_capture_ids={1})
    fake.add(activity_row(1, stamp(1)), capture(1))
    failures: dict[tuple[str, int], int] = {}

    for _ in range(4):
        with pytest.raises(LlamaSwapUnreachableError):
            poll_once(LlamaSwapApi(BASE_URL, fake.fetch), START, tmp_path, failures)

    assert load_cursor(tmp_path / "cursor.json") == START
    assert not (tmp_path / DAY_FILE).exists()


def test_the_cursor_is_saved_after_each_chunk(tmp_path: Path) -> None:
    fake = FakeLlamaSwap(unreachable_capture_ids={150})
    for row_id in range(1, 201):
        fake.add(activity_row(row_id, stamp(row_id)), capture(row_id))

    with pytest.raises(LlamaSwapUnreachableError):
        poll_once(LlamaSwapApi(BASE_URL, fake.fetch), START, tmp_path)

    assert load_cursor(tmp_path / "cursor.json").last_id == 149
    assert len(read_records(tmp_path / DAY_FILE)) == 149


def test_a_malformed_row_is_passed_after_three_polls_with_an_estimated_end(
    tmp_path: Path,
) -> None:
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, stamp(1)), capture(1))
    fake.add({**activity_row(2, stamp(2)), "timestamp": "not a time"})
    fake.add(activity_row(3, stamp(3)), capture(3))
    failures: dict[tuple[str, int], int] = {}
    api = LlamaSwapApi(BASE_URL, fake.fetch)

    for _ in range(2):
        with pytest.raises(LlamaSwapQueryError, match="row 2"):
            poll_once(api, load_cursor(tmp_path / "cursor.json"), tmp_path, failures)
    report = poll_once(api, load_cursor(tmp_path / "cursor.json"), tmp_path, failures)

    lines = read_records(tmp_path / DAY_FILE)
    assert [line["capture_status"] for line in lines] == ["saved", "error", "saved"]
    assert lines[1]["activity"]["timestamp"] == "not a time"  # type: ignore[index]
    assert lines[1]["estimated_end"] == stamp(1).isoformat(), "nearest neighbour, lower id on a tie"
    assert report.errors == 1
    assert report.cursor.last_id == 3
    assert report.cursor.last_ts == stamp(3)

    again = poll_once(api, load_cursor(tmp_path / "cursor.json"), tmp_path, failures)
    fresh = poll_once(api, load_cursor(tmp_path / "cursor.json"), tmp_path, {})

    for later in (again, fresh):  # same process, then a restart or a --once run
        assert (later.rows, later.id_reset) == (0, False)


def test_an_empty_host_label_is_a_usage_error(tmp_path: Path) -> None:
    argv = ["captures-pull", "--url", BASE_URL, "--host-label", "", "--out", str(tmp_path)]

    assert run_cli([*argv, "--once"], api_fetch=FakeLlamaSwap().fetch) == 2


def delegate(source_path: str) -> object:
    """Return a delegate run's root session with its transcript at `source_path`."""
    row = session_row("pi-run", at(10, 10), at(10, 30), models=(QWEN,), run_id="run-1")
    return dataclasses.replace(row, source_path=source_path, project="run-1")


LOCAL = "/workspace/alpha-project/.agentic/delegate/results/run-1/session/s.jsonl"
LANDED = "/srv/landing/mac/alpha-project/.agentic/delegate/results/run-1/session/s.jsonl"


@pytest.mark.parametrize(
    ("orchestrator", "path", "linked"),
    [
        (session_row("pi-orch", at(10), at(11), models=(QWEN,)), LANDED, True),
        (session_row("pi-orch", at(10, 15), at(11), models=(QWEN,)), LOCAL, False),
        (
            dataclasses.replace(
                session_row("pi-orch", at(10), at(11), models=(QWEN,)), project="beta-project"
            ),
            LOCAL,
            False,
        ),
    ],
    ids=["landing-zone-path", "started-after-the-run", "other-project"],
)
def test_delegate_link_needs_the_project_and_the_window(
    tmp_path: Path, orchestrator: object, path: str, linked: bool
) -> None:
    sessions = [orchestrator, delegate(path)]

    result = claims(tmp_path, sessions, [call(1, at(10, 20), model=QWEN, requested=QWEN)])  # type: ignore[arg-type]

    assert owners(result[1]) == ({"pi-run"} if linked else {"pi-orch", "pi-run"})


def test_non_chat_stream_events_are_shown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = sse([chunk({"content": "partial"}), {"error": {"message": "context overflow"}}])
    saved = record(activity_row(1, at(10, 5)), capture(1, response=body))
    captures = tmp_path / "captures"
    write_capture_file(captures / "gpu" / DAY_FILE, [saved])
    table = write_sessions(tmp_path / "s.parquet", [session_row("pi-a", at(10), at(10, 30))])

    run_cli(show_args(CaptureEvidence(captures, table), "--session", "pi-a", "--call", "1"))

    out = capsys.readouterr().out
    assert "context overflow" in out and "incomplete" in out


def test_show_reads_only_the_days_around_the_selection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    captures = tmp_path / "captures"
    write_capture_file(captures / "gpu" / DAY_FILE, [call(1, at(10, 5))])
    (captures / "gpu" / "2026-09-01.jsonl.zst").write_bytes(b"not zstd at all")
    table = write_sessions(tmp_path / "s.parquet", [session_row("pi-a", at(10), at(10, 30))])

    code = run_cli(show_args(CaptureEvidence(captures, table), "--session", "pi-a"))

    captured = capsys.readouterr()
    assert code == 0
    assert "10:05:00" in captured.out
    assert "2026-09-01" not in captured.err, "a far day's file is never opened"


@pytest.mark.parametrize(
    ("line", "message"),
    [(b"[1]\n", "line 1 is not a capture record"), (b"{oops\n", "line 1 is not JSON")],
    ids=["not-an-object", "not-json"],
)
def test_a_bad_line_names_its_file_and_line(tmp_path: Path, line: bytes, message: str) -> None:
    path = tmp_path / DAY_FILE
    path.write_bytes(zstandard.ZstdCompressor().compress(line))

    with pytest.raises(ValueError, match=message) as raised:
        read_records(path)

    assert str(path) in str(raised.value)


def test_show_names_an_unreadable_capture_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    captures = tmp_path / "captures"
    (captures / "gpu" / DAY_FILE).mkdir(parents=True)  # a directory where a file belongs
    table = write_sessions(tmp_path / "s.parquet", [session_row("pi-a", at(10), at(10, 30))])

    code = run_cli(show_args(CaptureEvidence(captures, table), "--date", "2026-10-22"))

    assert code == 1
    assert DAY_FILE in capsys.readouterr().err
