"""The same run ID reached through several inputs, exercised through `build_run_table`."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from run_table_builders import (
    LANDING_HOST,
    assistant_message,
    make_project,
    review_record,
    run_record,
    session_header,
    write_ledger,
    write_result_dir,
    write_session,
)

from agentic_telemetry.run_table.build import build_run_table
from agentic_telemetry.run_table.dedupe import ConflictingRunError


def _project_and_landing_copy(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Build a project root and a flattened landing copy of its delegate directory."""
    work = tmp_path / "work"
    delegate = make_project(work, "local-game")
    work_ledger = write_ledger(
        delegate,
        [run_record("run-shared"), review_record("run-shared", "pass")],
    )
    run_dir = write_result_dir(delegate, "run-shared")
    write_session(run_dir, [session_header(), assistant_message(tokens_in=9, tokens_out=1)])

    landing = tmp_path / "collector"
    copy = landing / "landing" / LANDING_HOST / "local-game"
    shutil.copytree(delegate, copy)
    return work, work_ledger, landing, copy / "ledger.jsonl"


def test_identical_run_through_two_inputs_appears_once(tmp_path: Path) -> None:
    work, work_ledger, landing, _ = _project_and_landing_copy(tmp_path)

    rows, _ = build_run_table([work, landing])

    assert [row.run_id for row in rows] == ["run-shared"]
    assert Path(rows[0].ledger_path).resolve() == work_ledger.resolve()


def test_first_discovered_input_supplies_the_kept_row(tmp_path: Path) -> None:
    work, _, landing, landing_ledger = _project_and_landing_copy(tmp_path)

    rows, _ = build_run_table([landing, work])

    assert len(rows) == 1
    assert Path(rows[0].ledger_path).resolve() == landing_ledger.resolve()


def test_differing_run_through_two_inputs_raises_naming_both_ledgers(tmp_path: Path) -> None:
    work, work_ledger, landing, landing_ledger = _project_and_landing_copy(tmp_path)
    write_ledger(
        landing_ledger.parent,
        [run_record("run-shared", outcome="integrity"), review_record("run-shared", "pass")],
    )

    with pytest.raises(ConflictingRunError) as excinfo:
        build_run_table([work, landing])

    message = str(excinfo.value)
    assert "run-shared" in message
    assert str(work_ledger) in message
    assert str(landing_ledger) in message


def test_stale_copy_missing_a_review_yields_the_reviewed_row_with_a_warning(
    tmp_path: Path,
) -> None:
    """Spec 0004 reverses 0001's default: a stale copy is no longer a conflict."""
    work, work_ledger, landing, landing_ledger = _project_and_landing_copy(tmp_path)
    write_ledger(landing_ledger.parent, [run_record("run-shared")])

    rows, summary = build_run_table([landing, work])

    assert [row.run_id for row in rows] == ["run-shared"]
    assert rows[0].review_count == 1
    assert Path(rows[0].ledger_path).resolve() == work_ledger.resolve()
    assert any(
        "run-shared" in warning and str(work_ledger) in warning and str(landing_ledger) in warning
        for warning in summary.warnings
    ), summary.warnings


def test_identical_duplicate_rows_inside_one_ledger_collapse(tmp_path: Path) -> None:
    root = tmp_path / "work"
    write_ledger(
        make_project(root, "proj"),
        [run_record("run-twice"), run_record("run-twice"), run_record("run-other")],
    )

    rows, _ = build_run_table([root])

    assert sorted(row.run_id for row in rows) == ["run-other", "run-twice"]


def test_differing_duplicate_rows_inside_one_ledger_raise(tmp_path: Path) -> None:
    root = tmp_path / "work"
    ledger = write_ledger(
        make_project(root, "proj"),
        [run_record("run-twice"), run_record("run-twice", outcome="needs_frontier")],
    )

    with pytest.raises(ConflictingRunError) as excinfo:
        build_run_table([root])

    assert "run-twice" in str(excinfo.value)
    assert str(ledger) in str(excinfo.value)


def test_distinct_runs_from_two_projects_are_all_kept(tmp_path: Path) -> None:
    root = tmp_path / "work"
    write_ledger(make_project(root, "one"), [run_record("run-1a"), run_record("run-1b")])
    write_ledger(make_project(root, "two", layout="landing"), [run_record("run-2a")])

    rows, _ = build_run_table([root])

    assert sorted((row.project, row.run_id) for row in rows) == [
        ("one", "run-1a"),
        ("one", "run-1b"),
        ("two", "run-2a"),
    ]


def test_orphan_reached_through_two_inputs_is_listed_once(tmp_path: Path) -> None:
    work = tmp_path / "work"
    delegate = make_project(work, "local-game")
    write_ledger(delegate, [run_record("run-shared")])
    write_result_dir(delegate, "run-orphan")
    landing = tmp_path / "collector"
    shutil.copytree(delegate, landing / "landing" / LANDING_HOST / "local-game")

    _, summary = build_run_table([work, landing])

    assert len(summary.orphans) == 1
    assert sum("run-orphan" in warning for warning in summary.warnings) == 1
