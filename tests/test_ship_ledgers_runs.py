"""`ship-ledgers.sh` across runs, copy failures and the remote (VM) mode (spec 0004).

A second run copies nothing new; nothing is deleted from the landing zone; one
failed copy is reported and the rest continue; an unreachable VM is a quiet skip;
the default remote destination is `telemetry.example:/srv/telemetry/landing/<host>/`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from ship_ledgers_helpers import (
    ShipEnv,
    describe,
    host_dir,
    make_ship_env,
    make_ship_project,
    output_lines,
    run_ship,
    stub_calls,
    write_claude_sessions,
    write_recording_stub,
    write_rsync_passthrough,
)

DEFAULT_VM = "telemetry.example"
DEFAULT_LANDING = "/srv/telemetry/landing"


@pytest.fixture
def env(tmp_path: Path) -> ShipEnv:
    """A synthetic world with two projects and one Claude Code session."""
    world = make_ship_env(tmp_path)
    make_ship_project(world.projects / "owner" / "alpha")
    make_ship_project(world.projects / "owner" / "broken")
    write_claude_sessions(world.home / ".claude" / "projects", "cc-0001")
    return world


def _inodes(root: Path) -> dict[str, int]:
    """Return `{relative path: inode}` for every file under `root`."""
    return {
        path.relative_to(root).as_posix(): path.stat().st_ino
        for path in root.rglob("*")
        if path.is_file()
    }


def test_second_run_recopies_nothing(env: ShipEnv) -> None:
    dest = env.root / "landing"
    first = run_ship(env, "--dest", str(dest))
    assert first.returncode == 0, describe(first)
    before = _inodes(host_dir(dest))
    assert before, "expected the first run to copy files"

    second = run_ship(env, "--dest", str(dest))

    assert second.returncode == 0, describe(second)
    assert _inodes(host_dir(dest)) == before, "expected every file left in place, not re-copied"


def test_file_deleted_at_the_source_survives_in_the_landing_zone(env: ShipEnv) -> None:
    dest = env.root / "landing"
    first = run_ship(env, "--dest", str(dest))
    assert first.returncode == 0, describe(first)
    session = env.home / ".claude" / "projects" / "-workspace-beta-project" / "cc-0001.jsonl"
    state = env.projects / "owner" / "alpha" / ".agentic/delegate/results/run-alpha/state.json"
    session.unlink()
    state.unlink()

    second = run_ship(env, "--dest", str(dest))

    assert second.returncode == 0, describe(second)
    host = host_dir(dest)
    assert (host / "_sessions/claude-code/-workspace-beta-project/cc-0001.jsonl").is_file()
    assert (host / "alpha/.agentic/delegate/results/run-alpha/state.json").is_file()


def test_failed_copy_is_reported_once_and_the_rest_still_ship(env: ShipEnv) -> None:
    write_rsync_passthrough(env, fail_when_arg_contains="/broken")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 1, describe(result)
    errors = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(errors) == 1, f"expected one stderr line for the failed copy\n{describe(result)}"
    assert "owner/broken" in errors[0], describe(result)
    host = host_dir(dest)
    assert (host / "alpha/.agentic/delegate/ledger.jsonl").is_file(), describe(result)
    assert (host / "_sessions/claude-code/-workspace-beta-project/cc-0001.jsonl").is_file()


def test_unreachable_vm_exits_0_with_one_line_and_no_copy(env: ShipEnv) -> None:
    write_recording_stub(env, "nc", exit_code=1)
    write_recording_stub(env, "rsync", exit_code=0)

    result = run_ship(env)

    assert result.returncode == 0, describe(result)
    lines = output_lines(result)
    assert len(lines) == 1, f"expected exactly one output line\n{describe(result)}"
    assert DEFAULT_VM in lines[0], describe(result)
    assert "skip" in lines[0], describe(result)
    assert len(stub_calls(env, "nc")) == 1, "expected one reachability probe"
    assert stub_calls(env, "rsync") == [], "expected no rsync when the VM is unreachable"
    assert stub_calls(env, "ssh") == [], "expected no ssh when the VM is unreachable"


def test_reachability_probe_targets_the_vm_ssh_port(env: ShipEnv) -> None:
    write_recording_stub(env, "nc", exit_code=1)
    write_recording_stub(env, "rsync", exit_code=0)

    result = run_ship(env)

    assert result.returncode == 0, describe(result)
    (probe,) = stub_calls(env, "nc")
    assert DEFAULT_VM in probe, f"expected the probe to name the VM, got {probe}"
    assert "22" in probe, f"expected the probe to target port 22, got {probe}"


def test_remote_mode_sends_each_project_to_the_default_vm_landing(env: ShipEnv) -> None:
    write_recording_stub(env, "nc", exit_code=0)
    write_recording_stub(env, "rsync", exit_code=0)

    result = run_ship(env)

    assert result.returncode == 0, describe(result)
    destinations = [call[-1] for call in stub_calls(env, "rsync") if call]
    shape = re.compile(
        rf"^{re.escape(DEFAULT_VM)}:{re.escape(DEFAULT_LANDING)}/(?P<host>[^/]+)/(?P<target>.+)$"
    )
    matched = [shape.match(destination) for destination in destinations]
    assert all(matched), f"expected every rsync destination on the VM landing, got {destinations}"
    hosts = {match.group("host") for match in matched if match}
    assert len(hosts) == 1, f"expected one host directory, got {hosts}"
    targets = {match.group("target") for match in matched if match}
    assert {"alpha/", "broken/"} <= targets, f"expected one rsync per project, got {targets}"
    assert "_sessions/claude-code/" in targets, f"expected the Claude Code copy, got {targets}"


def test_remote_mode_creates_the_landing_directory_with_one_ssh(env: ShipEnv) -> None:
    write_recording_stub(env, "nc", exit_code=0)
    write_recording_stub(env, "rsync", exit_code=0)

    result = run_ship(env)

    assert result.returncode == 0, describe(result)
    ssh_calls = stub_calls(env, "ssh")
    assert len(ssh_calls) == 1, f"expected a single ssh call, got {ssh_calls}"
    assert ssh_calls[0][0] == DEFAULT_VM, f"expected ssh to the VM, got {ssh_calls[0]}"
    assert any(arg.startswith(f"{DEFAULT_LANDING}/") for arg in ssh_calls[0]), ssh_calls[0]


def test_remote_mode_honours_telemetry_vm_and_landing(env: ShipEnv) -> None:
    write_recording_stub(env, "nc", exit_code=0)
    write_recording_stub(env, "rsync", exit_code=0)
    landing = "/srv/other-landing"

    result = run_ship(
        env, extra_env={"TELEMETRY_VM": "vm.telemetry.example", "TELEMETRY_LANDING": landing}
    )

    assert result.returncode == 0, describe(result)
    destinations = [call[-1] for call in stub_calls(env, "rsync") if call]
    assert destinations, describe(result)
    prefix = f"vm.telemetry.example:{landing}/"
    assert all(destination.startswith(prefix) for destination in destinations), destinations


def test_ssh_failure_after_a_good_probe_exits_1_naming_the_vm(env: ShipEnv) -> None:
    """The documented exit codes are 0, 1 and 2: a failed ssh is a named failure, not 255."""
    write_recording_stub(env, "nc", exit_code=0)
    write_recording_stub(env, "ssh", exit_code=255)
    write_recording_stub(env, "rsync", exit_code=0)

    result = run_ship(env)

    assert result.returncode == 1, describe(result)
    errors = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(errors) == 1, describe(result)
    assert DEFAULT_VM in errors[0] and errors[0].startswith("ship-ledgers:"), describe(result)
    assert stub_calls(env, "rsync") == [], "expected no copy when the landing cannot be created"


def test_unreachable_vm_still_exits_1_after_a_name_collision(env: ShipEnv) -> None:
    """D10: an offline run must not hide a collision the next monitor check would miss."""
    make_ship_project(env.projects / "first-owner" / "same-name")
    make_ship_project(env.projects / "second-owner" / "same-name")
    write_recording_stub(env, "rsync", exit_code=0)

    result = run_ship(env)

    assert result.returncode == 1, describe(result)
    assert any("same-name" in line for line in result.stderr.splitlines()), describe(result)
    assert any("skip" in line and DEFAULT_VM in line for line in output_lines(result))
    assert stub_calls(env, "rsync") == [], "expected no copy when the VM is unreachable"
    assert stub_calls(env, "ssh") == [], "expected no ssh when the VM is unreachable"
