"""`ship-ledgers.sh` ships exactly what the readers read, under `.agentic` (spec 0004).

From `.agentic/delegate/`: `ledger.jsonl` and each run's `result.json`,
`state.json` and `session/*.jsonl`. From `.agentic/telemetry/`: `*.jsonl`.
Nothing else: a delegate run's `pi-agent/` is a Pi home that can hold credentials.
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
    planted_files,
    relative_files,
    run_ship,
    shipped_files,
)


@pytest.fixture
def world(tmp_path: Path) -> tuple[ShipEnv, str]:
    """One project with planted noise; return the env and the project's run ID."""
    env = make_ship_env(tmp_path)
    return env, make_ship_project(env.projects / "owner" / "alpha")


def _ship(world: tuple[ShipEnv, str]) -> tuple[ShipEnv, Path, str]:
    """Run the script into `--dest`; return the env, the project's landing copy and run ID.

    The run happens in the test body, not the fixture, so a script failure is a
    test failure rather than a setup error.
    """
    env, run_id = world
    dest = env.root / "landing"
    result = run_ship(env, "--dest", str(dest))
    assert result.returncode == 0, describe(result)
    return env, host_dir(dest) / "alpha", run_id


def test_landing_copy_holds_exactly_the_reader_allowlist(
    world: tuple[ShipEnv, str],
) -> None:
    _, copy, run_id = _ship(world)

    assert relative_files(copy) == shipped_files(run_id)


@pytest.mark.parametrize(
    "fragment",
    [
        "pi-agent/auth.json",
        "pi-agent/sessions/pi-home.jsonl",
        "npm-cache/",
        "segments/",
        "harness.log",
        "delegate.log",
        "prompt.md",
        "handoff.json",
        "last-diagnostics.txt",
        "session/notes.txt",
        "telemetry/notes.txt",
        "README.md",
        "src/example_module.py",
    ],
)
def test_planted_file_is_not_copied(world: tuple[ShipEnv, str], fragment: str) -> None:
    _, copy, run_id = _ship(world)
    assert any(fragment in planted for planted in planted_files(run_id)), (
        f"test setup: {fragment!r} is not one of the planted files"
    )

    leaked = sorted(path for path in relative_files(copy) if fragment in path)

    assert leaked == [], f"expected {fragment!r} never to ship, found {leaked}"


def test_no_pi_agent_or_npm_cache_directory_is_created(
    world: tuple[ShipEnv, str],
) -> None:
    _, copy, _ = _ship(world)

    created = sorted(
        path.relative_to(copy).as_posix()
        for path in copy.rglob("*")
        if path.name in {"pi-agent", "npm-cache", "segments"}
    )

    assert created == [], f"expected rsync never to descend into these, found {created}"


def test_landing_copy_keeps_dot_agentic_in_the_path(world: tuple[ShipEnv, str]) -> None:
    _, copy, _ = _ship(world)

    assert (copy / ".agentic" / "delegate" / "ledger.jsonl").is_file()
    assert (copy / ".agentic" / "telemetry" / "tool-calls.jsonl").is_file()
    assert not (copy / "delegate").exists(), "expected no flattened delegate/ directory"
    assert not (copy / "telemetry").exists(), "expected no flattened telemetry/ directory"


def test_shipped_files_are_byte_identical_to_their_sources(
    world: tuple[ShipEnv, str],
) -> None:
    env, copy, run_id = _ship(world)
    source = env.projects / "owner" / "alpha"

    for relative in sorted(shipped_files(run_id)):
        assert (copy / relative).read_bytes() == (source / relative).read_bytes(), relative


def test_symlinks_inside_the_evidence_never_ship(world: tuple[ShipEnv, str]) -> None:
    """A link is neither followed nor copied, so a target's path or content never lands."""
    env, run_id = world
    secret = env.root / "outside" / "secret.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("SECRET-BYTES\n", encoding="utf-8")
    results = env.projects / "owner" / "alpha" / ".agentic" / "delegate" / "results"
    linked_run = results / "run-linked"
    linked_run.symlink_to(secret.parent, target_is_directory=True)
    (results / run_id / "session" / "linked.jsonl").symlink_to(secret)
    (results / "run-file-link").mkdir()
    (results / "run-file-link" / "result.json").symlink_to(secret)
    (results / "run-session-link").mkdir()
    (results / "run-session-link" / "session").symlink_to(secret.parent, target_is_directory=True)
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    shipped = list(host_dir(dest).rglob("*"))
    links = [path for path in shipped if path.is_symlink()]
    assert links == [], f"expected no symlink under --dest, got {links}"
    leaked = [p for p in shipped if p.is_file() and b"SECRET-BYTES" in p.read_bytes()]
    assert leaked == [], f"expected no file with the link target's content, got {leaked}"
    assert relative_files(host_dir(dest) / "alpha") == shipped_files(run_id), describe(result)
