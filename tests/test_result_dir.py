"""Per-run result directories: result.json, state.json, orphans, and the ledger/result join."""

from __future__ import annotations

from pathlib import Path

import pytest
from run_table_builders import (
    make_project,
    run_record,
    write_ledger,
    write_result_dir,
    write_session,
    write_state,
)

from agentic_telemetry.run_table.discovery import find_ledgers
from agentic_telemetry.run_table.join import assemble_ledger
from agentic_telemetry.run_table.result_dir import diff_keys, orphaned_result_dirs, scan_results


def test_scan_reads_result_json_for_each_run_directory(tmp_path: Path) -> None:
    delegate = make_project(tmp_path, "proj")
    recorded = run_record("run-with-result")
    write_result_dir(delegate, "run-with-result", result=recorded)

    results = scan_results(delegate / "results")

    assert set(results) == {"run-with-result"}
    assert results["run-with-result"].result_json == recorded
    assert results["run-with-result"].state is None


def test_scan_sums_retries_and_compactions_over_state_segments(tmp_path: Path) -> None:
    delegate = make_project(tmp_path, "proj")
    run_dir = write_result_dir(delegate, "run-handoff")
    write_state(run_dir, phase="implement", segments=[(1, 1), (2, 0), (0, 3)])

    state = scan_results(delegate / "results")["run-handoff"].state

    assert state is not None
    assert state.phase == "implement"
    assert state.retries == 3
    assert state.compactions == 4


def test_scan_lists_directory_without_result_json_with_no_result(tmp_path: Path) -> None:
    delegate = make_project(tmp_path, "proj")
    write_result_dir(delegate, "run-interrupted", extra_files=("prompt.md", "install.log"))

    results = scan_results(delegate / "results")

    assert results["run-interrupted"].result_json is None


def test_orphan_is_a_result_directory_with_no_ledger_row_and_no_result_json(
    tmp_path: Path,
) -> None:
    delegate = make_project(tmp_path, "proj")
    write_result_dir(delegate, "run-in-ledger")
    write_result_dir(delegate, "run-result-only", result=run_record("run-result-only"))
    orphan_dir = write_result_dir(delegate, "run-orphan", extra_files=("prompt.md", "harness.log"))
    write_session(orphan_dir, ["{}"])

    results = scan_results(delegate / "results")
    orphans = orphaned_result_dirs(results, {"run-in-ledger"})

    assert list(orphans) == ["run-orphan"]


def test_diff_keys_names_changed_added_and_missing_keys_in_sorted_order() -> None:
    ledger_row = {"outcome": "green", "gate": "pass", "reason": "same", "wall_seconds": 50}
    result_json = {"outcome": "integrity", "gate": "pass", "reason": "same", "extra": 1}

    assert diff_keys(ledger_row, result_json) == ["extra", "outcome", "wall_seconds"]


def test_diff_keys_is_empty_for_identical_objects() -> None:
    record = run_record("run-same")
    assert diff_keys(record, dict(record)) == []


def _assemble(tmp_path: Path):
    found = find_ledgers([tmp_path])
    assert len(found) == 1
    return assemble_ledger(found[0])


def test_result_json_fills_in_a_run_missing_from_the_ledger(tmp_path: Path) -> None:
    delegate = make_project(tmp_path, "proj")
    write_ledger(delegate, [run_record("run-in-ledger")])
    write_result_dir(
        delegate, "run-result-only", result=run_record("run-result-only", outcome="integrity")
    )

    result = _assemble(tmp_path)

    by_id = {row.run_id: row for row in result.rows}
    assert set(by_id) == {"run-in-ledger", "run-result-only"}
    assert by_id["run-result-only"].outcome == "integrity"
    assert list(result.warnings) == []


def test_ledger_row_wins_over_differing_result_json_with_a_warning(tmp_path: Path) -> None:
    delegate = make_project(tmp_path, "proj")
    write_ledger(delegate, [run_record("run-both", outcome="green", gate="pass")])
    write_result_dir(
        delegate,
        "run-both",
        result=run_record("run-both", outcome="integrity", gate="fail"),
    )

    result = _assemble(tmp_path)

    assert [row.outcome for row in result.rows] == ["green"]
    assert [row.gate for row in result.rows] == ["pass"]
    matching = [w for w in result.warnings if "run-both" in w]
    assert len(matching) == 1
    assert "outcome" in matching[0]
    assert "gate" in matching[0]
    assert "reason" not in matching[0]


def test_matching_result_json_adds_no_warning(tmp_path: Path) -> None:
    delegate = make_project(tmp_path, "proj")
    recorded = run_record("run-both")
    write_ledger(delegate, [recorded])
    write_result_dir(delegate, "run-both", result=recorded)

    result = _assemble(tmp_path)

    assert len(result.rows) == 1
    assert list(result.warnings) == []


def test_older_run_without_result_directory_is_a_row_with_null_session_columns(
    tmp_path: Path,
) -> None:
    delegate = make_project(tmp_path, "proj")
    write_ledger(delegate, [run_record("run-old")])

    result = _assemble(tmp_path)

    (row,) = result.rows
    assert row.run_id == "run-old"
    assert row.tokens_in is None
    assert row.tokens_out is None
    assert row.assistant_turns is None
    assert row.compactions is None
    assert row.retries is None


def test_orphaned_result_directory_is_not_a_row_and_is_reported(tmp_path: Path) -> None:
    delegate = make_project(tmp_path, "proj")
    write_ledger(delegate, [run_record("run-in-ledger")])
    write_result_dir(delegate, "run-orphan", extra_files=("prompt.md", "harness.log"))

    result = _assemble(tmp_path)

    assert [row.run_id for row in result.rows] == ["run-in-ledger"]
    assert list(result.orphans) == ["run-orphan"]
    assert any("run-orphan" in warning for warning in result.warnings)


def test_ledger_run_with_result_dir_but_no_result_json_is_not_orphaned(tmp_path: Path) -> None:
    delegate = make_project(tmp_path, "proj")
    write_ledger(delegate, [run_record("run-in-ledger")])
    write_result_dir(delegate, "run-in-ledger", extra_files=("prompt.md",))

    result = _assemble(tmp_path)

    assert [row.run_id for row in result.rows] == ["run-in-ledger"]
    assert list(result.orphans) == []


@pytest.mark.parametrize(
    ("file_name", "content"),
    [
        pytest.param("result.json", b"{truncated", id="result-bad-json"),
        pytest.param("result.json", b'{"run": "\xe2\x82"}', id="result-not-utf8"),
        pytest.param("result.json", b"[1, 2]", id="result-array"),
        pytest.param("state.json", b"{truncated", id="state-bad-json"),
        pytest.param("state.json", b"[]", id="state-array"),
    ],
)
def test_unreadable_result_or_state_file_warns_and_the_run_is_still_a_row(
    tmp_path: Path, file_name: str, content: bytes
) -> None:
    delegate = make_project(tmp_path, "proj")
    write_ledger(delegate, [run_record("run-a")])
    run_dir = write_result_dir(delegate, "run-a")
    bad_file = run_dir / file_name
    bad_file.write_bytes(content)

    result = _assemble(tmp_path)

    assert [row.run_id for row in result.rows] == ["run-a"]
    assert result.rows[0].compactions is None
    assert any(str(bad_file) in warning for warning in result.warnings)


def test_result_only_run_takes_its_id_and_evidence_from_the_directory_name(
    tmp_path: Path,
) -> None:
    delegate = make_project(tmp_path, "proj")
    write_ledger(delegate, [run_record("run-in-ledger")])
    run_dir = write_result_dir(
        delegate, "run-from-dir", result=run_record("run-in-ledger", outcome="integrity")
    )
    write_state(run_dir, phase="implement", segments=[(2, 1)])

    result = _assemble(tmp_path)

    by_id = {row.run_id: row for row in result.rows}
    assert set(by_id) == {"run-in-ledger", "run-from-dir"}
    assert by_id["run-from-dir"].retries == 2
    assert any("run-from-dir" in w and "run-in-ledger" in w for w in result.warnings)


def test_result_json_without_a_run_field_takes_the_directory_name(tmp_path: Path) -> None:
    delegate = make_project(tmp_path, "proj")
    write_ledger(delegate, [run_record("run-in-ledger")])
    write_result_dir(delegate, "run-from-dir", result=run_record("ignored", drop=("run",)))

    result = _assemble(tmp_path)

    assert sorted(row.run_id for row in result.rows) == ["run-from-dir", "run-in-ledger"]
    assert any("run-from-dir" in warning for warning in result.warnings)
