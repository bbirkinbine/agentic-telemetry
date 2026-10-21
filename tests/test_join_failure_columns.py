"""Per-ledger assembly: the derived delegated-phase and first-failure columns."""

from __future__ import annotations

from pathlib import Path

from run_table_builders import (
    make_project,
    review_record,
    run_record,
    write_ledger,
    write_result_dir,
    write_state,
)

from agentic_telemetry.run_table.discovery import find_ledgers
from agentic_telemetry.run_table.join import assemble_ledger


def _assemble(root: Path):
    found = find_ledgers([root])
    assert len(found) == 1
    return assemble_ledger(found[0])


def _only_row(root: Path):
    result = _assemble(root)
    assert len(result.rows) == 1
    return result.rows[0]


def test_green_run_with_defect_verdict_fails_at_review(tmp_path: Path) -> None:
    write_ledger(
        make_project(tmp_path, "proj"),
        [run_record("run-a"), review_record("run-a", "model-defect")],
    )

    row = _only_row(tmp_path)

    assert row.delegated_phase == "implement"
    assert row.first_failure_phase == "review"
    assert row.failure_cause == "model-defect"


def test_green_run_with_pass_verdict_has_no_failure(tmp_path: Path) -> None:
    write_ledger(
        make_project(tmp_path, "proj"),
        [run_record("run-a"), review_record("run-a", "pass")],
    )

    row = _only_row(tmp_path)

    assert row.first_failure_phase is None
    assert row.failure_cause is None


def test_integrity_handoff_fails_at_the_phase_from_state_json(tmp_path: Path) -> None:
    delegate = make_project(tmp_path, "proj")
    write_ledger(
        delegate,
        [run_record("run-h", harness="delegate-run-v2", bucket="phase:plan", outcome="integrity")],
    )
    write_state(write_result_dir(delegate, "run-h"), phase="test-first", segments=[(0, 0)])

    row = _only_row(tmp_path)

    assert row.delegated_phase == "test-first"
    assert row.first_failure_phase == "test-first"
    assert row.failure_cause == "integrity"


def test_handoff_without_state_json_takes_phase_from_bucket_prefix(tmp_path: Path) -> None:
    write_ledger(
        make_project(tmp_path, "proj"),
        [run_record("run-h", harness="delegate-run-v2", bucket="phase:plan", outcome="integrity")],
    )

    row = _only_row(tmp_path)

    assert row.delegated_phase == "plan"
    assert row.first_failure_phase == "plan"


def test_unknown_outcome_warns_naming_the_run(tmp_path: Path) -> None:
    write_ledger(make_project(tmp_path, "proj"), [run_record("run-odd", outcome="timeout")])

    result = _assemble(tmp_path)

    (row,) = result.rows
    assert row.first_failure_phase == "unknown"
    assert row.failure_cause == "timeout"
    assert any("run-odd" in w and "timeout" in w for w in result.warnings)


def test_unrecognized_bucket_warns_naming_the_run(tmp_path: Path) -> None:
    write_ledger(make_project(tmp_path, "proj"), [run_record("run-big", bucket="large")])

    result = _assemble(tmp_path)

    (row,) = result.rows
    assert row.delegated_phase is None
    assert any("run-big" in warning for warning in result.warnings)


def test_green_run_whose_latest_review_has_no_verdict_is_unknown(tmp_path: Path) -> None:
    write_ledger(
        make_project(tmp_path, "proj"),
        [run_record("run-a"), {"type": "review", "run": "run-a", "ts": "2026-10-20T12:00:00Z"}],
    )

    result = _assemble(tmp_path)

    (row,) = result.rows
    assert row.review_count == 1
    assert row.first_failure_phase == "unknown"
    assert row.failure_cause == "review-without-verdict"
    assert any("run-a" in warning and "verdict" in warning for warning in result.warnings)
