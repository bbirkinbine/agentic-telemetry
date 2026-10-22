"""`ship-ledgers.sh` options: `--help`, argument errors and `--dest` handling (spec 0004).

Argument errors exit 2 before anything is discovered or copied: a recording
`rsync` stub stands in for the real one in those tests, so any copy attempt shows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ship_ledgers_helpers import (
    SCRIPT,
    ShipEnv,
    describe,
    make_ship_env,
    make_ship_project,
    output_lines,
    run_ship,
    stub_calls,
    write_recording_stub,
)


@pytest.fixture
def env(tmp_path: Path) -> ShipEnv:
    """A synthetic world with one project, so a wrongly accepted call would copy."""
    world = make_ship_env(tmp_path)
    make_ship_project(world.projects / "owner" / "alpha")
    return world


def _assert_nothing_copied(env: ShipEnv) -> None:
    """No rsync, nc or ssh call was recorded."""
    for name in ("rsync", "nc", "ssh"):
        assert stub_calls(env, name) == [], f"expected no {name} call, got {stub_calls(env, name)}"


def test_help_exits_zero_and_names_the_options_and_project_root(env: ShipEnv) -> None:
    write_recording_stub(env, "rsync", exit_code=0)

    result = run_ship(env, "--help")

    assert result.returncode == 0, describe(result)
    for word in ("--dest", "--max-depth", "PROJECTS_ROOT"):
        assert word in result.stdout, f"expected --help to name {word}\n{describe(result)}"
    _assert_nothing_copied(env)


def test_help_names_the_vm_variables_and_client_session_overrides(env: ShipEnv) -> None:
    result = run_ship(env, "--help")

    assert result.returncode == 0, describe(result)
    for word in (
        "TELEMETRY_VM",
        "TELEMETRY_LANDING",
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
        "PI_CODING_AGENT_DIR",
        "PI_CODING_AGENT_SESSION_DIR",
    ):
        assert word in result.stdout, f"expected --help to name {word}\n{describe(result)}"


def test_unknown_option_exits_2_naming_the_option(env: ShipEnv) -> None:
    write_recording_stub(env, "rsync", exit_code=0)

    result = run_ship(env, "--frobnicate", "--dest", str(env.root / "landing"))

    assert result.returncode == 2, describe(result)
    assert "--frobnicate" in result.stdout + result.stderr, describe(result)
    _assert_nothing_copied(env)
    assert not (env.root / "landing").exists()


@pytest.mark.parametrize(
    ("args", "named"),
    [
        (["extra-positional"], "extra-positional"),
        (["--max-depth", "abc"], "--max-depth"),
        (["--max-depth", "0"], "--max-depth"),
        (["--max-depth", "-1"], "--max-depth"),
        (["--max-depth", "2.5"], "--max-depth"),
        (["--max-depth"], "--max-depth"),
        (["--dest", "--help"], "--dest"),
        (["--dest", "-landing"], "--dest"),
    ],
    ids=[
        "positional",
        "max-depth-text",
        "max-depth-zero",
        "max-depth-negative",
        "max-depth-fraction",
        "max-depth-missing-value",
        "dest-value-is-an-option",
        "dest-value-starts-with-dash",
    ],
)
def test_bad_argument_exits_2_naming_the_problem(env: ShipEnv, args: list[str], named: str) -> None:
    write_recording_stub(env, "rsync", exit_code=0)
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest), *args)

    assert result.returncode == 2, describe(result)
    assert named in result.stdout + result.stderr, describe(result)
    _assert_nothing_copied(env)
    assert not dest.exists(), f"expected no --dest after an argument error\n{describe(result)}"


def test_dest_without_a_value_exits_2_naming_dest(env: ShipEnv) -> None:
    write_recording_stub(env, "rsync", exit_code=0)

    result = run_ship(env, "--dest")

    assert result.returncode == 2, describe(result)
    assert "--dest" in result.stdout + result.stderr, describe(result)
    _assert_nothing_copied(env)


def test_dest_with_a_missing_parent_exits_2_and_creates_nothing(env: ShipEnv) -> None:
    write_recording_stub(env, "rsync", exit_code=0)
    missing_parent = env.root / "no-such-parent"

    result = run_ship(env, "--dest", str(missing_parent / "landing"))

    assert result.returncode == 2, describe(result)
    assert "no-such-parent" in result.stdout + result.stderr, describe(result)
    assert not missing_parent.exists(), "expected the missing parent to stay missing"
    _assert_nothing_copied(env)


def test_dest_naming_an_existing_file_exits_2(env: ShipEnv) -> None:
    write_recording_stub(env, "rsync", exit_code=0)
    a_file = env.root / "landing-is-a-file"
    a_file.write_text("not a directory\n", encoding="utf-8")

    result = run_ship(env, "--dest", str(a_file))

    assert result.returncode == 2, describe(result)
    assert "landing-is-a-file" in result.stdout + result.stderr, describe(result)
    assert a_file.read_text(encoding="utf-8") == "not a directory\n"
    _assert_nothing_copied(env)


def test_missing_dest_is_created_and_shipped_into(env: ShipEnv) -> None:
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    assert dest.is_dir(), f"expected --dest to be created\n{describe(result)}"
    hosts = [child for child in dest.iterdir() if child.is_dir()]
    assert len(hosts) == 1, f"expected one host directory, got {hosts}\n{describe(result)}"
    assert (hosts[0] / "alpha" / ".agentic" / "delegate" / "ledger.jsonl").is_file(), describe(
        result
    )


def test_dest_mode_never_probes_or_contacts_the_vm(env: ShipEnv) -> None:
    result = run_ship(env, "--dest", str(env.root / "landing"))

    assert result.returncode == 0, describe(result)
    assert stub_calls(env, "nc") == [], "expected no reachability probe with --dest"
    assert stub_calls(env, "ssh") == [], "expected no ssh with --dest"
    assert all("unreachable" not in line for line in output_lines(result)), describe(result)


def test_script_never_passes_delete_to_rsync() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "--delete" not in text, "expected no --delete: the landing zone keeps pruned files"
