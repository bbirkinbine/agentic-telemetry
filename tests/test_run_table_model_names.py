"""Transcript columns on run-table rows, with no metrics query (spec 0005, D1 and D2)."""

from __future__ import annotations

from pathlib import Path

from server_join_helpers import ALIAS_QWEN, QWEN, SERVER_NULL_COLUMNS, write_server_project

from agentic_telemetry.run_table.build import build_run_table
from agentic_telemetry.run_table.row import RunRow


def built_rows(tmp_path: Path) -> dict[str, RunRow]:
    root = tmp_path / "evidence"
    write_server_project(root)
    rows, _ = build_run_table([root])
    return {row.run_id: row for row in rows}


def test_run_with_a_transcript_records_requested_and_served_names(tmp_path: Path) -> None:
    row = built_rows(tmp_path)["alpha-run-joined"]

    assert tuple(row.requested_models or ()) == (ALIAS_QWEN,)
    assert tuple(row.served_models or ()) == (QWEN,)


def test_run_with_a_transcript_records_its_peak_turn_context(tmp_path: Path) -> None:
    row = built_rows(tmp_path)["alpha-run-joined"]

    assert row.peak_turn_prompt_tokens == 700


def test_run_without_a_transcript_has_null_transcript_columns(tmp_path: Path) -> None:
    row = built_rows(tmp_path)["alpha-run-sybil"]

    assert row.requested_models is None
    assert row.served_models is None
    assert row.peak_turn_prompt_tokens is None


def test_built_rows_are_not_queried_with_null_server_columns(tmp_path: Path) -> None:
    for run_id, row in built_rows(tmp_path).items():
        assert row.server_status == "not_queried", run_id
        for name in SERVER_NULL_COLUMNS:
            assert getattr(row, name) is None, (run_id, name)
