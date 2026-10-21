"""`agentic-telemetry run-table` end to end through `main(argv)`."""

from __future__ import annotations

import importlib.metadata
import re
import shutil
from pathlib import Path

import duckdb
import pytest
from run_table_builders import (
    LANDING_HOST,
    EvidenceTree,
    make_project,
    run_record,
    write_ledger,
)

from agentic_telemetry.cli import main


def run_main(argv: list[str]) -> int:
    """Return the exit code whether `main` returns it or argparse raises SystemExit."""
    try:
        return main(argv)
    except SystemExit as exit_signal:
        code = exit_signal.code
        return code if isinstance(code, int) else 1


def summary_has(stdout: str, keyword: str, count: int) -> bool:
    """True when one summary line mentions `keyword` together with the standalone `count`.

    Warning lines that quote a `ledger.jsonl:N` location are skipped so a line
    number cannot pass for a count.
    """
    number = re.compile(rf"(?<![\w.:/-]){count}(?![\w.:/-])")
    return any(
        keyword in line.lower() and ".jsonl" not in line and number.search(line)
        for line in stdout.splitlines()
    )


def test_writes_one_parquet_row_per_run(evidence_tree: EvidenceTree, tmp_path: Path) -> None:
    out_dir = tmp_path / "data"
    out_dir.mkdir()
    out = out_dir / "run_table.parquet"

    code = run_main(["run-table", "--out", str(out), str(evidence_tree.root)])

    assert code == 0
    run_ids = sorted(row[0] for row in duckdb.read_parquet(str(out)).fetchall())
    assert run_ids == sorted(evidence_tree.alpha_run_ids + evidence_tree.beta_run_ids)


def test_summary_reports_counts_orphans_and_every_warning(
    evidence_tree: EvidenceTree, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "run_table.parquet"

    code = run_main(["run-table", "--out", str(out), str(evidence_tree.root)])

    stdout = capsys.readouterr().out
    assert code == 0
    assert summary_has(stdout, "ledger", 2), "ledgers found"
    assert summary_has(stdout, "alpha", len(evidence_tree.alpha_run_ids)), "alpha rows"
    assert summary_has(stdout, "beta", len(evidence_tree.beta_run_ids)), "beta rows"
    assert summary_has(stdout, "review", evidence_tree.reviews_joined), "reviews joined"
    assert summary_has(stdout, "token", evidence_tree.runs_with_tokens), "runs with tokens"
    assert evidence_tree.orphan_run_id in stdout
    assert f"{evidence_tree.alpha_ledger}:{evidence_tree.malformed_line_no}" in stdout
    assert evidence_tree.ghost_review_run_id in stdout


def test_summary_never_prints_reason_text(
    evidence_tree: EvidenceTree, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "run_table.parquet"

    run_main(["run-table", "--out", str(out), str(evidence_tree.root)])

    captured = capsys.readouterr()
    assert "reason-text-not-for-stdout" not in captured.out
    assert "reason-text-not-for-stdout" not in captured.err


def test_existing_output_file_is_rebuilt(evidence_tree: EvidenceTree, tmp_path: Path) -> None:
    out = tmp_path / "run_table.parquet"
    out.write_bytes(b"stale content from an earlier build")

    code = run_main(["run-table", "--out", str(out), str(evidence_tree.root)])

    assert code == 0
    expected = len(evidence_tree.alpha_run_ids) + len(evidence_tree.beta_run_ids)
    assert len(duckdb.read_parquet(str(out)).fetchall()) == expected


def test_nonexistent_input_directory_is_a_usage_error(tmp_path: Path) -> None:
    out = tmp_path / "run_table.parquet"

    code = run_main(["run-table", "--out", str(out), str(tmp_path / "missing-dir")])

    assert code == 2
    assert not out.exists()


def test_input_that_is_a_file_is_a_usage_error(tmp_path: Path) -> None:
    a_file = tmp_path / "not-a-dir.txt"
    a_file.write_text("x\n", encoding="utf-8")
    out = tmp_path / "run_table.parquet"

    code = run_main(["run-table", "--out", str(out), str(a_file)])

    assert code == 2
    assert not out.exists()


@pytest.mark.parametrize(
    "out_name",
    [
        pytest.param("run_table.csv", id="not-parquet-suffix"),
        pytest.param("no-such-parent/run_table.parquet", id="parent-missing"),
        pytest.param("existing-dir.parquet", id="out-is-a-directory"),
    ],
)
def test_bad_out_path_is_a_usage_error(
    evidence_tree: EvidenceTree, tmp_path: Path, out_name: str
) -> None:
    (tmp_path / "existing-dir.parquet").mkdir()
    out = tmp_path / out_name

    code = run_main(["run-table", "--out", str(out), str(evidence_tree.root)])

    assert code == 2
    assert not (tmp_path / "no-such-parent").exists()
    assert not (tmp_path / "run_table.csv").exists()
    assert (tmp_path / "existing-dir.parquet").is_dir()


def test_missing_out_option_is_a_usage_error(evidence_tree: EvidenceTree) -> None:
    assert run_main(["run-table", str(evidence_tree.root)]) == 2


def test_directory_without_a_ledger_exits_1_naming_it_and_writes_nothing(
    evidence_tree: EvidenceTree, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "no-ledger-here"
    empty.mkdir()
    out_dir = tmp_path / "data"
    out_dir.mkdir()
    out = out_dir / "run_table.parquet"

    code = run_main(["run-table", "--out", str(out), str(evidence_tree.root), str(empty)])

    assert code == 1
    assert str(empty) in capsys.readouterr().err
    assert list(out_dir.iterdir()) == []


def test_conflicting_duplicate_run_exits_1_naming_both_ledgers_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    work_ledger = write_ledger(make_project(work, "local-game"), [run_record("run-shared")])
    collector = tmp_path / "collector"
    copy = collector / "landing" / LANDING_HOST / "local-game"
    shutil.copytree(work_ledger.parent, copy)
    landing_ledger = write_ledger(copy, [run_record("run-shared", outcome="needs_frontier")])
    out_dir = tmp_path / "data"
    out_dir.mkdir()
    out = out_dir / "run_table.parquet"

    code = run_main(["run-table", "--out", str(out), str(work), str(collector)])

    err = capsys.readouterr().err
    assert code == 1
    assert str(work_ledger) in err
    assert str(landing_ledger) in err
    assert list(out_dir.iterdir()) == []


def test_run_table_help_states_intent(capsys: pytest.CaptureFixture[str]) -> None:
    code = run_main(["run-table", "--help"])

    help_text = " ".join(capsys.readouterr().out.lower().split())
    assert code == 0
    assert "delegate ledger" in help_text
    assert "read-only" in help_text
    assert "--out" in help_text


def test_top_level_help_lists_the_run_table_command(capsys: pytest.CaptureFixture[str]) -> None:
    code = run_main(["--help"])

    assert code == 0
    assert "run-table" in capsys.readouterr().out


def test_console_script_entry_point_runs_cli_main() -> None:
    scripts = importlib.metadata.entry_points(group="console_scripts")
    matches = [ep for ep in scripts if ep.name == "agentic-telemetry"]

    assert len(matches) == 1
    assert matches[0].value == "agentic_telemetry.cli:main"
    assert matches[0].load() is main
