"""Synthetic evidence and a `main` driver for the `captures` CLI tests (spec 0008).

`main(argv, api_fetch=...)` is the pinned seam: `agentic_telemetry.cli.main`
takes an `ApiFetch` for `captures-pull`, as it takes `fetch` for `--metrics-url`.

The evidence: one host directory `captures/gpu/` holding one day of records, and
a session table with an orchestrator and its subagent, a delegate run and its
subagent, two overlapping unrelated sessions, and two roots sharing a run id.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from capture_builders import (
    QWEN,
    activity_row,
    at,
    capture,
    chat_request,
    record,
    session_row,
    text_stream,
    write_capture_file,
    write_sessions,
)

from agentic_telemetry.captures.llama_swap_api import ApiFetch
from agentic_telemetry.cli import main

# Distinctive numbers, so a list line can be matched to its call.
FIRST_TOKENS = (4321, 765)
FIRST_DURATION_MS = 6543
SECOND_TOKENS = (1111, 222)


@dataclass(frozen=True)
class CaptureEvidence:
    """Paths of the synthetic captures root and session table."""

    captures: Path
    session_table: Path


def run_cli(argv: list[str], api_fetch: ApiFetch | None = None) -> int:
    """Return the exit code whether `main` returns it or argparse raises SystemExit."""
    try:
        if api_fetch is None:
            return main(argv)
        return main(argv, api_fetch=api_fetch)
    except SystemExit as exit_signal:
        code = exit_signal.code
        return code if isinstance(code, int) else 1


def _call(
    row_id: int,
    end: datetime,
    pieces: tuple[str, ...] = ("ok",),
    tokens: tuple[int, int] = (100, 20),
    duration_ms: int = 1500,
) -> dict[str, object]:
    """Return a saved record whose streamed answer is `pieces` joined."""
    activity = activity_row(
        row_id,
        end,
        model=QWEN,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        duration_ms=duration_ms,
    )
    return record(activity, capture(row_id, request=chat_request(), response=text_stream(*pieces)))


def make_capture_evidence(root: Path) -> CaptureEvidence:
    """Write the captures root and session table under `root`."""
    records = [
        _call(1, at(10, 5), ("Hel", "lo wor", "ld"), FIRST_TOKENS, FIRST_DURATION_MS),
        _call(2, at(10, 15), ("child ", "work")),
        _call(3, at(10, 25), ("Second ", "answer"), SECOND_TOKENS),
        _call(4, at(11, 5)),
        _call(5, at(12, 20)),
        _call(6, at(13)),
    ]
    captures = root / "captures"
    write_capture_file(captures / "gpu" / "2026-10-22.jsonl.zst", records)
    sessions = [
        session_row("pi-main", at(10), at(10, 30)),
        session_row("pi-sub", at(10, 10), at(10, 20), parent="pi-main"),
        session_row("pi-run-root", at(11), at(11, 30), run_id="run-0001"),
        session_row("pi-run-sub", at(11, 10), at(11, 20), parent="pi-run-root", run_id="run-0001"),
        session_row("pi-amb-a", at(12), at(12, 30)),
        session_row("pi-amb-b", at(12, 10), at(12, 40)),
        session_row("pi-dup-1", at(14), at(14, 10), run_id="run-dup"),
        session_row("pi-dup-2", at(14, 20), at(14, 30), run_id="run-dup"),
    ]
    table = write_sessions(root / "session_table.parquet", sessions)
    return CaptureEvidence(captures=captures, session_table=table)


def show_args(evidence: CaptureEvidence, *selector: str) -> list[str]:
    """Return `captures show` argv for `evidence` plus the selector arguments."""
    return [
        "captures",
        "show",
        "--captures",
        str(evidence.captures),
        "--session-table",
        str(evidence.session_table),
        *selector,
    ]


def lines_with(text: str, needle: str) -> list[str]:
    """Return the lines of `text` that contain `needle`."""
    return [line for line in text.splitlines() if needle in line]


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    """Return a header value by case-insensitive name."""
    lowered = {key.lower(): value for key, value in headers.items()}
    return lowered.get(name.lower())
