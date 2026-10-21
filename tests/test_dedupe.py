"""The same run ID reached through several inputs, exercised through `build_run_table`."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from run_table_builders import (
    INBOX_HOST,
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


def _project_and_inbox_copy(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Build a project root and a flattened inbox copy of its delegate directory."""
    work = tmp_path / "work"
    delegate = make_project(work, "local-game")
    work_ledger = write_ledger(
        delegate,
        [run_record("run-shared"), review_record("run-shared", "pass")],
    )
    run_dir = write_result_dir(delegate, "run-shared")
    write_session(run_dir, [session_header(), assistant_message(tokens_in=9, tokens_out=1)])

    inbox = tmp_path / "collector"
    copy = inbox / "inbox" / INBOX_HOST / "local-game"
    shutil.copytree(delegate, copy)
    return work, work_ledger, inbox, copy / "ledger.jsonl"


def test_identical_run_through_two_inputs_appears_once(tmp_path: Path) -> None:
    work, work_ledger, inbox, _ = _project_and_inbox_copy(tmp_path)

    rows, _ = build_run_table([work, inbox])

    assert [row.run_id for row in rows] == ["run-shared"]
    assert Path(rows[0].ledger_path).resolve() == work_ledger.resolve()


def test_first_discovered_input_supplies_the_kept_row(tmp_path: Path) -> None:
    work, _, inbox, inbox_ledger = _project_and_inbox_copy(tmp_path)

    rows, _ = build_run_table([inbox, work])

    assert len(rows) == 1
    assert Path(rows[0].ledger_path).resolve() == inbox_ledger.resolve()


def test_differing_run_through_two_inputs_raises_naming_both_ledgers(tmp_path: Path) -> None:
    work, work_ledger, inbox, inbox_ledger = _project_and_inbox_copy(tmp_path)
    write_ledger(
        inbox_ledger.parent,
        [run_record("run-shared", outcome="integrity"), review_record("run-shared", "pass")],
    )

    with pytest.raises(ConflictingRunError) as excinfo:
        build_run_table([work, inbox])

    message = str(excinfo.value)
    assert "run-shared" in message
    assert str(work_ledger) in message
    assert str(inbox_ledger) in message


def test_stale_copy_missing_a_review_is_a_conflict(tmp_path: Path) -> None:
    work, _, inbox, inbox_ledger = _project_and_inbox_copy(tmp_path)
    write_ledger(inbox_ledger.parent, [run_record("run-shared")])

    with pytest.raises(ConflictingRunError):
        build_run_table([work, inbox])


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
    write_ledger(make_project(root, "two", layout="inbox"), [run_record("run-2a")])

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
    inbox = tmp_path / "collector"
    shutil.copytree(delegate, inbox / "inbox" / INBOX_HOST / "local-game")

    _, summary = build_run_table([work, inbox])

    assert len(summary.orphans) == 1
    assert sum("run-orphan" in warning for warning in summary.warnings) == 1
