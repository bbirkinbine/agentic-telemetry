"""Join and display fixes from the spec 0008 reviews (`### Review resolutions`).

The child keeps only calls in its unpadded window; a delegate run's root session
is a child of the one session that orchestrated it; reasoning sent under both
keys counts once; a stream with no chat-completions chunk is shown raw; and
`captures show` warns about a torn frame instead of failing.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from capture_builders import (
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
from capture_join_helpers import call, claims, owners, seconds

from agentic_telemetry.captures.sse import reassemble
from agentic_telemetry.session_table.row import SessionRow

RESULTS = "/workspace/alpha-project/.agentic/delegate/results/run-1/session/s.jsonl"


def test_parent_keeps_its_calls_in_the_childs_pad(tmp_path: Path) -> None:
    sessions = [
        session_row("pi-parent", at(10), at(10, 40), models=(QWEN,)),
        session_row("pi-child", at(10, 10), at(10, 20), models=(QWEN,), parent="pi-parent"),
    ]
    before = call(1, at(10, 10) - seconds(1), model=QWEN, requested=QWEN, duration_ms=3000)
    inside = call(2, at(10, 15), model=QWEN, requested=QWEN)
    after = call(3, at(10, 20) + seconds(3), model=QWEN, requested=QWEN, duration_ms=2000)

    result = claims(tmp_path, sessions, [before, inside, after])

    assert owners(result[1]) == {"pi-parent"}, "ended 1 s before the child started"
    assert owners(result[2]) == {"pi-child"}
    assert owners(result[3]) == {"pi-parent"}, "started 1 s after the child ended"
    assert not [item for item in result.values() if set(item.flags)]


def delegate(session_id: str = "pi-run") -> SessionRow:
    """Return a delegate run's root session whose results sit in `alpha-project`."""
    row = session_row(session_id, at(10, 10), at(10, 30), models=(QWEN,), run_id="run-1")
    return dataclasses.replace(
        row, source_path=RESULTS, cwd="/workspace/handoff/run-1", project="run-1"
    )


def test_delegate_run_is_a_child_of_its_orchestrator(tmp_path: Path) -> None:
    sessions = [session_row("pi-orch", at(10), at(11), models=(QWEN,)), delegate()]
    records = [
        call(1, at(10, 5), model=QWEN, requested=QWEN),
        call(2, at(10, 20), model=QWEN, requested=QWEN),
    ]

    result = claims(tmp_path, sessions, records)

    assert owners(result[1]) == {"pi-orch"}
    assert owners(result[2]) == {"pi-run"}
    assert not set(result[2].flags)


def test_two_possible_orchestrators_link_nothing(tmp_path: Path) -> None:
    sessions = [
        session_row("pi-orch-a", at(10), at(11), models=(QWEN,)),
        session_row("pi-orch-b", at(10, 1), at(11), models=(QWEN,)),
        delegate(),
    ]

    result = claims(tmp_path, sessions, [call(1, at(10, 20), model=QWEN, requested=QWEN)])

    assert owners(result[1]) == {"pi-orch-a", "pi-orch-b", "pi-run"}
    assert set(result[1].flags) == {"ambiguous"}


def test_session_view_lists_a_linked_delegate_run_as_a_child(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    captures = tmp_path / "captures"
    write_capture_file(
        captures / "gpu" / "2026-10-22.jsonl.zst", [call(1, at(10, 20), requested=QWEN)]
    )
    table = write_sessions(
        tmp_path / "s.parquet", [session_row("pi-orch", at(10), at(11), models=(QWEN,)), delegate()]
    )

    code = run_cli(show_args(CaptureEvidence(captures, table), "--session", "pi-orch"))

    assert code == 0
    assert "child pi-run: 1 calls" in capsys.readouterr().out


def test_reasoning_sent_under_both_keys_counts_once() -> None:
    body = sse([chunk({"reasoning_content": "think", "reasoning": "think"}), "[DONE]"])

    assert reassemble(body, "text/event-stream").reasoning == "think"


def test_stream_without_chat_chunks_is_raw_and_printed_as_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = sse([{"content": "plain completion text", "stop": False}, "[DONE]"])
    assert reassemble(body, "text/event-stream").kind == "raw"
    saved = record(activity_row(1, at(10, 5)), capture(1, response=body))
    captures = tmp_path / "captures"
    write_capture_file(captures / "gpu" / "2026-10-22.jsonl.zst", [saved])
    table = write_sessions(tmp_path / "s.parquet", [session_row("pi-a", at(10), at(10, 30))])

    code = run_cli(show_args(CaptureEvidence(captures, table), "--session", "pi-a", "--call", "1"))

    assert code == 0
    assert "plain completion text" in capsys.readouterr().out


def test_show_warns_about_a_torn_frame_and_still_lists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    captures = tmp_path / "captures"
    path = write_capture_file(captures / "gpu" / "2026-10-22.jsonl.zst", [call(1, at(10, 5))])
    with path.open("ab") as handle:
        handle.write(b"\x28\xb5\x2f\xfd\x00\x01 torn")
    table = write_sessions(tmp_path / "s.parquet", [session_row("pi-a", at(10), at(10, 30))])

    code = run_cli(show_args(CaptureEvidence(captures, table), "--date", "2026-10-22"))

    captured = capsys.readouterr()
    assert code == 0
    assert "10:05:00" in captured.out
    assert "unreadable zstd frame" in captured.err
