"""`ship-ledgers.sh` stays bounded and safe against the VM (spec 0006, D7-D9).

- D7: ssh joins rsync's remote arguments into one command string that the VM's
  shell splits, so in remote mode a project whose directory name has anything
  outside `[A-Za-z0-9._-]` is skipped with one named line (exit 1). `--dest` mode
  still ships any name.
- D8: macOS `nc -w` is an idle timeout, so the reachability probe also passes
  Apple's `-G 3` connect timeout.
- D9: every ssh (the landing `mkdir` and rsync's remote shell) bounds connect
  and idle time, so a stalled link fails within about a minute instead of
  blocking later launchd cycles.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
from ship_ledgers_helpers import (
    ShipEnv,
    describe,
    make_ship_env,
    make_ship_project,
    run_ship,
    stub_calls,
    write_recording_stub,
)

TIMEOUTS = ("ConnectTimeout=10", "ServerAliveInterval=15", "ServerAliveCountMax=4")


@pytest.fixture
def env(tmp_path: Path) -> ShipEnv:
    """A synthetic world with one plainly named project and a reachable VM."""
    world = make_ship_env(tmp_path)
    make_ship_project(world.projects / "owner" / "alpha")
    write_recording_stub(world, "nc", exit_code=0)
    write_recording_stub(world, "rsync", exit_code=0)
    return world


def ssh_options(words: list[str]) -> set[str]:
    """Return the values of every `-o VALUE` or `-oVALUE` in an ssh argv."""
    found: set[str] = set()
    for index, word in enumerate(words):
        if word == "-o" and index + 1 < len(words):
            found.add(words[index + 1])
        elif word.startswith("-o") and len(word) > 2:
            found.add(word[2:])
    return found


def remote_shell(argv: list[str]) -> list[str]:
    """Return rsync's `-e`/`--rsh` remote shell split into words, or [] when absent."""
    for index, word in enumerate(argv):
        if word in ("-e", "--rsh") and index + 1 < len(argv):
            return shlex.split(argv[index + 1])
        if word.startswith("--rsh="):
            return shlex.split(word.removeprefix("--rsh="))
    return []


def test_landing_mkdir_ssh_bounds_connect_and_idle_time(env: ShipEnv) -> None:
    result = run_ship(env)

    assert result.returncode == 0, describe(result)
    (argv,) = stub_calls(env, "ssh")
    missing = set(TIMEOUTS) - ssh_options(argv)
    assert not missing, f"expected ssh options {sorted(missing)}, got {argv}"


def test_every_rsync_remote_shell_bounds_connect_and_idle_time(env: ShipEnv) -> None:
    result = run_ship(env)

    assert result.returncode == 0, describe(result)
    rsync_calls = stub_calls(env, "rsync")
    assert rsync_calls, describe(result)
    for argv in rsync_calls:
        missing = set(TIMEOUTS) - ssh_options(remote_shell(argv))
        assert not missing, f"expected {sorted(missing)} in rsync's remote shell, got {argv}"


def test_reachability_probe_has_a_connect_timeout(env: ShipEnv) -> None:
    run_ship(env)

    (probe,) = stub_calls(env, "nc")
    joined = " ".join(probe)
    assert "-G 3" in joined or "-G3" in probe, f"expected nc -G 3 (connect timeout), got {probe}"


@pytest.mark.parametrize("name", ["my project", "semi;colon", "dollar$sign", "quote'd"])
def test_unsafe_project_name_is_skipped_in_remote_mode(env: ShipEnv, name: str) -> None:
    make_ship_project(env.projects / "owner" / name)

    result = run_ship(env)

    assert result.returncode == 1, describe(result)
    errors = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(errors) == 1 and name in errors[0], describe(result)
    copied = [argv for argv in stub_calls(env, "rsync") if any(name in arg for arg in argv)]
    assert copied == [], f"expected no copy of {name!r}, got {copied}"
    targets = [argv[-1] for argv in stub_calls(env, "rsync")]
    assert any(target.endswith("/alpha/") for target in targets), targets
