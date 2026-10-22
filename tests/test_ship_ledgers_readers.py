"""The readers give the same rows from `landing/<host>/` as from the sources (spec 0004).

The script ships synthetic projects and HOME session directories with `--dest`;
`build_run_table` and `build_session_table` then read the landing copy. Rows match
the source build apart from where they were read (`ledger_path`, `source_path`).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from ship_ledgers_helpers import (
    ShipEnv,
    describe,
    host_dir,
    make_ship_env,
    make_ship_project,
    run_ship,
    write_claude_sessions,
    write_codex_sessions,
    write_pi_sessions,
)

from agentic_telemetry.run_table.build import build_run_table
from agentic_telemetry.run_table.row import RunRow
from agentic_telemetry.session_table.build import build_session_table
from agentic_telemetry.session_table.row import SessionRow


@pytest.fixture
def env(tmp_path: Path) -> ShipEnv:
    """Two projects and every client's sessions in the synthetic HOME."""
    env = make_ship_env(tmp_path)
    make_ship_project(env.projects / "first-owner" / "alpha")
    make_ship_project(env.projects / "second-owner" / "beta")
    write_claude_sessions(env.home / ".claude" / "projects", "cc-0001")
    write_pi_sessions(env.home / ".pi" / "agent" / "sessions", "pi-0001")
    write_codex_sessions(env.home / ".codex" / "sessions", "codex-plain", "codex-zst")
    return env


def _ship(env: ShipEnv) -> Path:
    """Run the script into `--dest` in the test body and return the host directory."""
    dest = env.root / "landing"
    result = run_ship(env, "--dest", str(dest))
    assert result.returncode == 0, describe(result)
    return host_dir(dest)


def _without_location(rows: list[RunRow]) -> list[RunRow]:
    """Return rows with `ledger_path` blanked, sorted by run ID."""
    return sorted(
        (dataclasses.replace(row, ledger_path="") for row in rows), key=lambda row: row.run_id
    )


def _session_keys(rows: list[SessionRow]) -> set[tuple[str, str, str | None]]:
    """Return `(client, session_id, run_id)` for every session row."""
    return {(row.client, row.session_id, row.run_id) for row in rows}


def test_run_table_from_the_landing_copy_matches_the_source_projects(
    env: ShipEnv,
) -> None:
    host = _ship(env)

    source_rows, source_summary = build_run_table([env.projects])
    landing_rows, landing_summary = build_run_table([host])

    assert len(source_rows) == 2
    assert _without_location(landing_rows) == _without_location(source_rows)
    assert landing_summary.reviews_joined == source_summary.reviews_joined
    assert landing_summary.runs_with_tokens == source_summary.runs_with_tokens


def test_run_table_project_is_the_project_basename_on_the_landing_copy(
    env: ShipEnv,
) -> None:
    host = _ship(env)

    rows, summary = build_run_table([host])

    assert sorted((row.project, row.run_id) for row in rows) == [
        ("alpha", "run-alpha"),
        ("beta", "run-beta"),
    ]
    assert summary.rows_per_project == {"alpha": 1, "beta": 1}


def test_run_table_reads_ledger_paths_under_dot_agentic_in_the_landing_copy(
    env: ShipEnv,
) -> None:
    host = _ship(env)

    rows, _ = build_run_table([host])

    expected = {
        str(host / name / ".agentic" / "delegate" / "ledger.jsonl") for name in ("alpha", "beta")
    }
    assert {row.ledger_path for row in rows} == expected


def test_session_table_from_the_landing_copy_has_the_source_keys(
    env: ShipEnv,
) -> None:
    host = _ship(env)
    sources = [
        env.home / ".claude" / "projects",
        env.home / ".pi" / "agent" / "sessions",
        env.home / ".codex" / "sessions",
        env.projects,
    ]

    source_rows, _ = build_session_table(sources)
    landing_rows, _ = build_session_table([host])

    expected = {
        ("claude-code", "cc-0001", None),
        ("pi", "pi-0001", None),
        ("codex", "codex-plain", None),
        ("codex", "codex-zst", None),
        ("pi", "pi-run-alpha", "run-alpha"),
        ("pi", "pi-run-beta", "run-beta"),
    }
    assert _session_keys(source_rows) >= expected
    assert _session_keys(landing_rows) == _session_keys(source_rows)
