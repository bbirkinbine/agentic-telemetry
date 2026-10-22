"""Discovery: which ledger.jsonl files count, and which project each belongs to."""

from __future__ import annotations

from pathlib import Path

import pytest
from run_table_builders import LANDING_HOST, make_project, run_record, write_ledger

from agentic_telemetry.run_table.discovery import NoLedgerError, find_ledgers, project_name


def _resolved(paths: list[Path]) -> set[Path]:
    return {path.resolve() for path in paths}


def test_finds_ledger_whose_parent_is_named_delegate(tmp_path: Path) -> None:
    delegate_dir = tmp_path / "proj" / ".agentic" / "delegate"
    ledger = write_ledger(delegate_dir, [run_record()])

    found = find_ledgers([tmp_path])

    assert _resolved([item.path for item in found]) == {ledger.resolve()}


def test_finds_ledger_whose_parent_contains_a_results_directory(tmp_path: Path) -> None:
    flattened = make_project(tmp_path, "proj", layout="landing")
    ledger = write_ledger(flattened, [run_record()])

    found = find_ledgers([tmp_path])

    assert _resolved([item.path for item in found]) == {ledger.resolve()}
    assert found[0].results_dir is not None
    assert found[0].results_dir.resolve() == (flattened / "results").resolve()


def test_ignores_ledger_without_delegate_parent_or_results_sibling(tmp_path: Path) -> None:
    write_ledger(tmp_path / "proj" / "notes", [run_record()])
    kept = write_ledger(tmp_path / "proj" / ".agentic" / "delegate", [run_record()])

    found = find_ledgers([tmp_path])

    assert _resolved([item.path for item in found]) == {kept.resolve()}


def test_project_is_the_directory_that_contains_dot_agentic(tmp_path: Path) -> None:
    write_ledger(make_project(tmp_path, "local-game", layout="agentic"), [run_record()])

    found = find_ledgers([tmp_path])

    assert [item.project for item in found] == ["local-game"]


def test_project_is_the_ledger_parent_when_landing_flattens_the_path(tmp_path: Path) -> None:
    write_ledger(make_project(tmp_path, "local-game", layout="landing"), [run_record()])

    found = find_ledgers([tmp_path])

    assert [item.project for item in found] == ["local-game"]


def test_project_uses_dot_agentic_parent_inside_an_landing_copy(tmp_path: Path) -> None:
    copy_root = tmp_path / "landing" / LANDING_HOST / "local-game"
    ledger = copy_root / ".agentic" / "delegate" / "ledger.jsonl"
    assert project_name(ledger) == "local-game"


def test_landing_host_dir_names_each_project_after_its_dot_agentic_parent(
    tmp_path: Path,
) -> None:
    """Spec 0004: `landing/<host>/<project>/.agentic/delegate/ledger.jsonl` keeps `project`."""
    host = tmp_path / "landing" / LANDING_HOST
    for name in ("local-game", "other-game"):
        write_ledger(host / name / ".agentic" / "delegate", [run_record(f"run-{name}")])
    sessions = host / "_sessions" / "pi" / "--workspace--"
    sessions.mkdir(parents=True)
    (sessions / "session.jsonl").write_text("{}\n", encoding="utf-8")

    found = find_ledgers([host])

    assert sorted(item.project for item in found) == ["local-game", "other-game"]
    assert "delegate" not in {item.project for item in found}


def test_project_name_falls_back_to_ledger_parent_directory_name(tmp_path: Path) -> None:
    ledger = tmp_path / "landing" / LANDING_HOST / "local-game" / "ledger.jsonl"
    assert project_name(ledger) == "local-game"


def test_finds_ledgers_in_every_given_directory(tmp_path: Path) -> None:
    first = write_ledger(make_project(tmp_path / "a", "one"), [run_record()])
    second = write_ledger(make_project(tmp_path / "b", "two", layout="landing"), [run_record()])

    found = find_ledgers([tmp_path / "a", tmp_path / "b"])

    assert _resolved([item.path for item in found]) == {first.resolve(), second.resolve()}
    assert sorted(item.project for item in found) == ["one", "two"]


def test_overlapping_directories_count_each_ledger_once(tmp_path: Path) -> None:
    ledger = write_ledger(make_project(tmp_path, "proj"), [run_record()])

    found = find_ledgers([tmp_path, tmp_path / "proj", tmp_path / "proj" / ".agentic"])

    assert len(found) == 1
    assert found[0].path.resolve() == ledger.resolve()


def test_directory_with_no_ledger_raises_naming_the_directory(tmp_path: Path) -> None:
    empty = tmp_path / "nothing-here"
    empty.mkdir()

    with pytest.raises(NoLedgerError) as excinfo:
        find_ledgers([empty])

    assert str(empty) in str(excinfo.value)


def test_one_directory_without_a_ledger_fails_even_when_another_has_one(tmp_path: Path) -> None:
    write_ledger(make_project(tmp_path / "good", "proj"), [run_record()])
    empty = tmp_path / "empty-dir"
    empty.mkdir()

    with pytest.raises(NoLedgerError) as excinfo:
        find_ledgers([tmp_path / "good", empty])

    assert str(empty) in str(excinfo.value)


def test_ignored_ledger_alone_counts_as_no_ledger(tmp_path: Path) -> None:
    write_ledger(tmp_path / "notes", [run_record()])

    with pytest.raises(NoLedgerError) as excinfo:
        find_ledgers([tmp_path])

    assert str(tmp_path) in str(excinfo.value)


def test_relative_dot_directory_names_the_project_and_an_absolute_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_ledger(make_project(tmp_path, "local-game", layout="agentic"), [run_record()])
    monkeypatch.chdir(tmp_path / "local-game")

    (found,) = find_ledgers([Path()])

    assert found.project == "local-game"
    assert found.path.is_absolute()


def test_relative_dot_directory_names_an_landing_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flattened = make_project(tmp_path, "local-game", layout="landing")
    write_ledger(flattened, [run_record()])
    monkeypatch.chdir(flattened)

    (found,) = find_ledgers([Path()])

    assert found.project == "local-game"


def test_ledger_inside_a_delegate_directory_results_is_not_a_second_ledger(
    tmp_path: Path,
) -> None:
    delegate = make_project(tmp_path, "proj")
    kept = write_ledger(delegate, [run_record()])
    write_ledger(delegate / "results" / "run-a" / "delegate", [run_record()])

    found = find_ledgers([tmp_path])

    assert _resolved([item.path for item in found]) == {kept.resolve()}


@pytest.mark.parametrize("skipped", ["node_modules", ".venv", ".git"])
def test_ledgers_under_skipped_directories_are_ignored(tmp_path: Path, skipped: str) -> None:
    kept = write_ledger(make_project(tmp_path, "proj"), [run_record()])
    write_ledger(tmp_path / "proj" / skipped / "pkg" / ".agentic" / "delegate", [run_record()])

    found = find_ledgers([tmp_path])

    assert _resolved([item.path for item in found]) == {kept.resolve()}


def test_relative_dot_dot_directory_names_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_ledger(make_project(tmp_path, "local-game", layout="agentic"), [run_record()])
    docs = tmp_path / "local-game" / "docs"
    docs.mkdir()
    monkeypatch.chdir(docs)

    (found,) = find_ledgers([Path("..")])

    assert found.project == "local-game"
    assert ".." not in found.path.parts
