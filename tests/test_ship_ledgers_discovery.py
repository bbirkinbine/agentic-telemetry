"""`ship-ledgers.sh` project discovery under `PROJECTS_ROOT` (spec 0004).

A project is a directory holding `.agentic/delegate/` or `.agentic/telemetry/`,
found up to `--max-depth` (default 3) below `PROJECTS_ROOT`, never inside `.git`,
`node_modules`, a hidden directory or another project. Its name is its basename.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ship_ledgers_helpers import (
    ShipEnv,
    describe,
    host_dir,
    make_ship_env,
    make_ship_project,
    output_lines,
    relative_files,
    run_ship,
    write_claude_sessions,
)

LEDGER = Path(".agentic") / "delegate" / "ledger.jsonl"


@pytest.fixture
def env(tmp_path: Path) -> ShipEnv:
    """An empty synthetic world."""
    return make_ship_env(tmp_path)


def _shipped_projects(dest: Path) -> set[str]:
    """Return the project directory names under the host directory, `_sessions` aside."""
    return {
        child.name
        for child in host_dir(dest).iterdir()
        if child.is_dir() and child.name != "_sessions"
    }


def test_project_two_levels_below_the_root_is_shipped(env: ShipEnv) -> None:
    make_ship_project(env.projects / "owner" / "alpha")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    assert (host_dir(dest) / "alpha" / LEDGER).is_file(), describe(result)


def test_default_depth_reaches_three_levels_but_not_four(env: ShipEnv) -> None:
    make_ship_project(env.projects / "a" / "b" / "depth-three")
    make_ship_project(env.projects / "a" / "b" / "c" / "depth-four")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    assert _shipped_projects(dest) == {"depth-three"}, describe(result)


def test_project_deeper_than_max_depth_is_not_shipped(env: ShipEnv) -> None:
    make_ship_project(env.projects / "shallow")
    make_ship_project(env.projects / "owner" / "deeper")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest), "--max-depth", "1")

    assert result.returncode == 0, describe(result)
    assert _shipped_projects(dest) == {"shallow"}, describe(result)


@pytest.mark.parametrize(
    "hiding_parent",
    [
        Path("owner") / ".git",
        Path("owner") / "node_modules",
        Path(".hidden"),
        Path("owner") / ".cache",
    ],
    ids=["git", "node-modules", "hidden-top-level", "hidden-nested"],
)
def test_projects_under_skipped_directories_are_not_found(
    env: ShipEnv, hiding_parent: Path
) -> None:
    make_ship_project(env.projects / "owner" / "visible")
    make_ship_project(env.projects / hiding_parent / "concealed")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    assert _shipped_projects(dest) == {"visible"}, describe(result)


def test_project_nested_inside_a_found_project_is_not_shipped_separately(env: ShipEnv) -> None:
    make_ship_project(env.projects / "outer")
    make_ship_project(env.projects / "outer" / "inner")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    assert _shipped_projects(dest) == {"outer"}, describe(result)
    nested = [path for path in relative_files(host_dir(dest)) if "inner" in path.split("/")]
    assert nested == [], f"expected nothing from the nested project, got {nested}"


def test_project_with_only_telemetry_is_found(env: ShipEnv) -> None:
    make_ship_project(env.projects / "owner" / "telemetry-only", delegate=False)
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    telemetry = host_dir(dest) / "telemetry-only" / ".agentic" / "telemetry" / "tool-calls.jsonl"
    assert telemetry.is_file(), describe(result)


def test_directory_without_agentic_evidence_is_not_shipped(env: ShipEnv) -> None:
    make_ship_project(env.projects / "owner" / "alpha")
    plain = env.projects / "owner" / "plain-repo" / ".agentic" / "notes"
    plain.mkdir(parents=True)
    (plain / "todo.jsonl").write_text("{}\n", encoding="utf-8")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    assert _shipped_projects(dest) == {"alpha"}, describe(result)


def test_basename_collision_skips_only_the_colliders_and_exits_1(env: ShipEnv) -> None:
    """D6: name both paths, ship every other project and the transcripts, exit 1."""
    make_ship_project(env.projects / "first-owner" / "same-name")
    make_ship_project(env.projects / "second-owner" / "same-name")
    make_ship_project(env.projects / "first-owner" / "unique")
    write_claude_sessions(env.home / ".claude" / "projects", "cc-0001")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 1, describe(result)
    errors = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(errors) == 1, describe(result)
    assert "first-owner/same-name" in errors[0], describe(result)
    assert "second-owner/same-name" in errors[0], describe(result)
    assert _shipped_projects(dest) == {"unique"}, describe(result)
    assert (host_dir(dest) / "_sessions" / "claude-code").is_dir(), describe(result)


def test_missing_projects_root_exits_2_naming_it_before_any_copy(env: ShipEnv) -> None:
    """D5: a mistyped root is a usage error, not a silent run with no projects."""
    write_claude_sessions(env.home / ".claude" / "projects", "cc-0001")
    missing = env.root / "no-such-root"
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest), extra_env={"PROJECTS_ROOT": str(missing)})

    assert result.returncode == 2, describe(result)
    assert str(missing) in result.stdout + result.stderr, describe(result)
    copied = [path for path in dest.rglob("*") if path.is_file()] if dest.exists() else []
    assert copied == [], f"expected nothing copied, got {copied}"


def test_root_without_projects_says_so_and_still_ships_transcripts(env: ShipEnv) -> None:
    """D5: an empty or unreadable root prints one line; transcripts still ship, exit 0."""
    (env.projects / "owner" / "not-a-project").mkdir(parents=True)
    write_claude_sessions(env.home / ".claude" / "projects", "cc-0001")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    lines = [line for line in output_lines(result) if "no projects found" in line]
    assert len(lines) == 1 and str(env.projects) in lines[0], describe(result)
    assert (host_dir(dest) / "_sessions" / "claude-code").is_dir(), describe(result)


def test_symlinked_project_directory_is_not_followed(env: ShipEnv) -> None:
    make_ship_project(env.root / "elsewhere" / "linked")
    (env.projects / "owner").mkdir(parents=True)
    (env.projects / "owner" / "linked").symlink_to(env.root / "elsewhere" / "linked")
    make_ship_project(env.projects / "owner" / "real")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    assert _shipped_projects(dest) == {"real"}, describe(result)


def test_project_path_with_a_space_ships(env: ShipEnv) -> None:
    make_ship_project(env.projects / "an owner" / "my project")
    dest = env.root / "landing dir"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    assert (host_dir(dest) / "my project" / LEDGER).is_file(), describe(result)


@pytest.mark.parametrize(
    "linked",
    [".agentic", ".agentic/delegate", ".agentic/telemetry"],
    ids=["agentic", "delegate", "telemetry"],
)
def test_symlinked_evidence_directory_is_skipped_loudly(env: ShipEnv, linked: str) -> None:
    """D9: a linked `.agentic` would be found and then silently copied as nothing."""
    make_ship_project(env.root / "elsewhere" / "linked-project")
    project = env.projects / "owner" / "linked-project"
    project.mkdir(parents=True)
    if linked == ".agentic":
        (project / ".agentic").symlink_to(env.root / "elsewhere" / "linked-project" / ".agentic")
    else:
        (project / ".agentic").mkdir()
        (project / linked).symlink_to(env.root / "elsewhere" / "linked-project" / linked)
    make_ship_project(env.projects / "owner" / "real")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 1, describe(result)
    errors = [line for line in result.stderr.splitlines() if "symlink" in line]
    assert len(errors) == 1 and "owner/linked-project" in errors[0], describe(result)
    assert _shipped_projects(dest) == {"real"}, describe(result)
    assert "shipped linked-project" not in result.stdout, describe(result)


def test_projects_root_that_is_a_file_exits_2_saying_so(env: ShipEnv) -> None:
    root_file = env.root / "root-file"
    root_file.write_text("not a directory\n", encoding="utf-8")

    result = run_ship(
        env, "--dest", str(env.root / "landing"), extra_env={"PROJECTS_ROOT": str(root_file)}
    )

    assert result.returncode == 2, describe(result)
    assert "not a directory" in result.stdout + result.stderr, describe(result)
