"""`ship-ledgers.sh` copies client transcripts to `<host>/_sessions/<client>/` (spec 0004).

Source directories resolve like `session_table/sources.py` (D2): Pi from
`PI_CODING_AGENT_SESSION_DIR`, else `PI_CODING_AGENT_DIR/sessions`, else
`~/.pi/agent/sessions`; Claude Code from `CLAUDE_CONFIG_DIR/projects`; Codex from
`CODEX_HOME/sessions`. Only `*.jsonl` ships, plus Codex `*.jsonl.zst`.
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
    relative_files,
    run_ship,
    write_claude_sessions,
    write_codex_sessions,
    write_pi_sessions,
)


@pytest.fixture
def env(tmp_path: Path) -> ShipEnv:
    """A synthetic world with one project, so the host directory always exists."""
    world = make_ship_env(tmp_path)
    make_ship_project(world.projects / "owner" / "alpha")
    return world


def _sessions(dest: Path, client: str) -> set[str]:
    """Return the files shipped for one client, relative to its `_sessions` directory."""
    return relative_files(host_dir(dest) / "_sessions" / client)


def test_default_client_directories_ship_jsonl_and_codex_zst_only(env: ShipEnv) -> None:
    claude = write_claude_sessions(env.home / ".claude" / "projects", "cc-0001")
    pi = write_pi_sessions(env.home / ".pi" / "agent" / "sessions", "pi-0001")
    codex = write_codex_sessions(env.home / ".codex" / "sessions", "codex-plain", "codex-zst")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    assert _sessions(dest, "claude-code") == claude
    assert _sessions(dest, "pi") == pi
    assert _sessions(dest, "codex") == codex
    assert any(name.endswith(".jsonl.zst") for name in _sessions(dest, "codex"))


def test_client_credentials_beside_the_session_directories_never_ship(env: ShipEnv) -> None:
    write_claude_sessions(env.home / ".claude" / "projects", "cc-0001")
    write_pi_sessions(env.home / ".pi" / "agent" / "sessions", "pi-0001")
    write_codex_sessions(env.home / ".codex" / "sessions", "codex-plain", "codex-zst")
    for secret in (".claude/.credentials.json", ".pi/agent/auth.json", ".codex/auth.json"):
        (env.home / secret).write_text('{"token": "placeholder"}\n', encoding="utf-8")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    leaked = sorted(str(path) for path in dest.rglob("*") if "auth" in path.name)
    leaked += sorted(str(path) for path in dest.rglob(".credentials.json"))
    assert leaked == [], f"expected no client credential file under --dest, got {leaked}"


def test_missing_client_directories_are_skipped_silently(env: ShipEnv) -> None:
    claude = write_claude_sessions(env.home / ".claude" / "projects", "cc-0001")
    dest = env.root / "landing"

    result = run_ship(env, "--dest", str(dest))

    assert result.returncode == 0, describe(result)
    assert _sessions(dest, "claude-code") == claude
    assert result.stderr.strip() == "", describe(result)
    mentions = [
        line for line in output_lines(result) if re.search(r"\b(pi|codex)\b", line, re.IGNORECASE)
    ]
    assert mentions == [], f"expected no line about the missing clients, got {mentions}"


def test_session_and_config_overrides_replace_the_home_defaults(env: ShipEnv) -> None:
    write_claude_sessions(env.home / ".claude" / "projects", "cc-decoy")
    write_pi_sessions(env.home / ".pi" / "agent" / "sessions", "pi-decoy")
    write_codex_sessions(env.home / ".codex" / "sessions", "codex-decoy", "codex-decoy-zst")
    claude_config = env.root / "claude-config"
    pi_sessions = env.root / "pi-session-dir"
    codex_home = env.root / "codex-home"
    claude = write_claude_sessions(claude_config / "projects", "cc-override")
    pi = write_pi_sessions(pi_sessions, "pi-override")
    codex = write_codex_sessions(codex_home / "sessions", "codex-override", "codex-override-zst")
    pi_agent_dir = env.root / "pi-agent-dir"
    write_pi_sessions(pi_agent_dir / "sessions", "pi-agent-dir-loses")
    dest = env.root / "landing"

    result = run_ship(
        env,
        "--dest",
        str(dest),
        extra_env={
            "CLAUDE_CONFIG_DIR": str(claude_config),
            "PI_CODING_AGENT_SESSION_DIR": str(pi_sessions),
            "PI_CODING_AGENT_DIR": str(pi_agent_dir),
            "CODEX_HOME": str(codex_home),
        },
    )

    assert result.returncode == 0, describe(result)
    assert _sessions(dest, "claude-code") == claude
    assert _sessions(dest, "pi") == pi
    assert _sessions(dest, "codex") == codex


def test_pi_agent_dir_override_ships_its_sessions_subdirectory(env: ShipEnv) -> None:
    write_pi_sessions(env.home / ".pi" / "agent" / "sessions", "pi-decoy")
    pi_agent_dir = env.root / "pi-agent-dir"
    pi = write_pi_sessions(pi_agent_dir / "sessions", "pi-agent-dir-0001")
    (pi_agent_dir / "auth.json").write_text('{"token": "placeholder"}\n', encoding="utf-8")
    dest = env.root / "landing"

    result = run_ship(
        env, "--dest", str(dest), extra_env={"PI_CODING_AGENT_DIR": str(pi_agent_dir)}
    )

    assert result.returncode == 0, describe(result)
    assert _sessions(dest, "pi") == pi
