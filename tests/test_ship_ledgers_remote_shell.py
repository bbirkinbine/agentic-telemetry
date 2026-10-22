"""`ship-ledgers.sh` talks to the VM in SSH batch mode (spec 0006).

Under launchd nobody can answer a password or host-key prompt, so every ssh the
script starts carries `-o BatchMode=yes`: the landing `mkdir` and the remote
shell of every rsync. An unusable key then fails at once. `--dest` mode copies
locally and passes rsync no remote shell at all; with the real rsync behind the
stub it also exercises bash 3.2's empty-array handling on the Mac.
"""

from __future__ import annotations

import itertools
import shlex
from pathlib import Path

import pytest
from ship_ledgers_helpers import (
    ShipEnv,
    describe,
    host_dir,
    make_ship_env,
    make_ship_project,
    run_ship,
    stub_calls,
    write_claude_sessions,
    write_recording_stub,
    write_rsync_passthrough,
)

DEFAULT_VM = "telemetry.example"
BATCH_MODE = "BatchMode=yes"
# ssh(1) options that take a value; the VM is the first argument after these.
SSH_OPTIONS_WITH_VALUE = frozenset("BbcDEeFIiJLlmOoPpQRSWw")


@pytest.fixture
def env(tmp_path: Path) -> ShipEnv:
    """A synthetic world with one project and one Claude Code session."""
    world = make_ship_env(tmp_path)
    make_ship_project(world.projects / "owner" / "alpha")
    write_claude_sessions(world.home / ".claude" / "projects", "cc-0001")
    return world


def ssh_host_index(argv: list[str]) -> int:
    """Return the index of the first non-option argument of an ssh argv (the host).

    Raises:
        AssertionError: when every argument is an option or an option's value.
    """
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            index += 1
            break
        if not arg.startswith("-") or arg == "-":
            break
        if len(arg) == 2 and arg[1] in SSH_OPTIONS_WITH_VALUE:
            index += 2
        else:
            index += 1
    assert index < len(argv), f"expected a host argument in ssh argv {argv}"
    return index


def has_batch_mode(options: list[str]) -> bool:
    """Return True when `options` holds `-o BatchMode=yes` or `-oBatchMode=yes`."""
    pairs = itertools.pairwise(options)
    return f"-o{BATCH_MODE}" in options or any(
        flag == "-o" and value == BATCH_MODE for flag, value in pairs
    )


def remote_shells(argv: list[str]) -> list[str]:
    """Return every remote-shell value in an rsync argv (`-e VALUE` or `--rsh=VALUE`)."""
    shells: list[str] = []
    for position, arg in enumerate(argv):
        if arg == "-e" and position + 1 < len(argv):
            shells.append(argv[position + 1])
        elif arg.startswith("--rsh="):
            shells.append(arg.removeprefix("--rsh="))
        elif arg == "--rsh" and position + 1 < len(argv):
            shells.append(argv[position + 1])
    return shells


def run_remote(env: ShipEnv) -> None:
    """Run the script in remote mode against a reachable VM with recording stubs."""
    write_recording_stub(env, "nc", exit_code=0)
    write_recording_stub(env, "rsync", exit_code=0)
    result = run_ship(env)
    assert result.returncode == 0, describe(result)


def test_landing_mkdir_ssh_runs_in_batch_mode_before_the_host(env: ShipEnv) -> None:
    run_remote(env)

    ssh_calls = stub_calls(env, "ssh")
    assert len(ssh_calls) == 1, f"expected a single ssh call, got {ssh_calls}"
    argv = ssh_calls[0]
    host_at = ssh_host_index(argv)
    assert argv[host_at] == DEFAULT_VM, f"expected the VM as ssh's host, got {argv}"
    assert has_batch_mode(argv[:host_at]), (
        f"expected -o {BATCH_MODE} before the host, so a prompt fails at once, got {argv}"
    )


def test_every_remote_rsync_uses_a_batch_mode_ssh_shell(env: ShipEnv) -> None:
    run_remote(env)

    rsync_calls = stub_calls(env, "rsync")
    assert len(rsync_calls) >= 2, f"expected a project and a session copy, got {rsync_calls}"
    for argv in rsync_calls:
        shells = remote_shells(argv)
        assert len(shells) == 1, f"expected one -e/--rsh remote shell, got {shells} in {argv}"
        words = shlex.split(shells[0])
        assert words and words[0] == "ssh", f"expected the remote shell to be ssh, got {words}"
        assert has_batch_mode(words[1:]), (
            f"expected -o {BATCH_MODE} in the remote shell, got {shells[0]!r}"
        )


def test_dest_mode_passes_no_remote_shell_and_still_copies(env: ShipEnv) -> None:
    write_rsync_passthrough(env)
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    rsync_calls = stub_calls(env, "rsync")
    assert rsync_calls, f"expected rsync to run in --dest mode\n{describe(result)}"
    for argv in rsync_calls:
        assert remote_shells(argv) == [], f"expected no -e/--rsh with --dest, got {argv}"
    host = host_dir(dest)
    assert (host / "alpha/.agentic/delegate/ledger.jsonl").is_file(), describe(result)
    assert (host / "_sessions/claude-code/-workspace-beta-project/cc-0001.jsonl").is_file()


def test_help_names_batch_mode(env: ShipEnv) -> None:
    result = run_ship(env, "--help")

    assert result.returncode == 0, describe(result)
    assert "BatchMode" in result.stdout, f"expected --help to name BatchMode\n{describe(result)}"
