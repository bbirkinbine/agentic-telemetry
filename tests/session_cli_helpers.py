"""Helpers for driving `agentic-telemetry session-table` through `main(argv)`."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb

from agentic_telemetry.cli import main

CLIENT_PATTERNS = {
    "pi": r"(?<![\w-])pi(?![\w-])",
    "claude-code": r"(?<![\w-])claude-code(?![\w-])",
    "codex": r"(?<![\w-])codex(?![\w-])",
}


def run_main(argv: list[str]) -> int:
    """Return the exit code whether `main` returns it or argparse raises SystemExit."""
    try:
        return main(argv)
    except SystemExit as exit_signal:
        code = exit_signal.code
        return code if isinstance(code, int) else 1


def summary_has(stdout: str, pattern: str, count: int) -> bool:
    """True when one non-warning summary line matches `pattern` and holds the standalone `count`.

    Lines that quote a `.jsonl` path are skipped so a `file:line` location cannot
    pass for a count.
    """
    keyword = re.compile(pattern, re.IGNORECASE)
    number = re.compile(rf"(?<![\w.:/-]){count}(?![\w.:/-])")
    return any(
        keyword.search(line) and ".jsonl" not in line and number.search(line)
        for line in stdout.splitlines()
    )


def source_status_lines(stdout: str, client: str) -> list[str]:
    """Return the summary lines that name `client` together with found / not found."""
    pattern = re.compile(CLIENT_PATTERNS[client])
    return [
        line.lower()
        for line in stdout.splitlines()
        if pattern.search(line) and "found" in line.lower()
    ]


def read_rows(out: Path) -> dict[tuple[str, str], dict[str, object]]:
    """Return the written Parquet rows keyed by (client, session_id)."""
    relation = duckdb.read_parquet(str(out))
    columns = list(relation.columns)
    records = [dict(zip(columns, values, strict=True)) for values in relation.fetchall()]
    return {(str(record["client"]), str(record["session_id"])): record for record in records}
