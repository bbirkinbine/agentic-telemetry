"""The stdout summary: identical warnings print once and are counted once."""

from __future__ import annotations

from agentic_telemetry.session_table.summary import SessionSummary, render_summary


def test_identical_warnings_print_once_and_count_once() -> None:
    summary = SessionSummary(
        warnings=["a.jsonl:3: skipped", "b.jsonl:1: skipped", "a.jsonl:3: skipped"]
    )

    text = render_summary(summary)

    assert text.count("a.jsonl:3: skipped") == 1
    assert "warnings: 2" in text
