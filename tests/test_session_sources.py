"""Default session locations: per-client environment overrides, precedence, found or not.

`default_sources(environ, home)` is a pure function of its two arguments, so every
test passes a dict as the environment and `tmp_path` as the home directory; the
real home and environment are never read.
"""

from __future__ import annotations

from pathlib import Path

from agentic_telemetry.session_table.sources import default_sources


def _by_client(environ: dict[str, str], home: Path) -> dict[str, tuple[Path, bool]]:
    return {
        str(source.client): (source.path, source.found) for source in default_sources(environ, home)
    }


def test_one_source_per_client() -> None:
    clients = [str(source.client) for source in default_sources({}, Path("/nonexistent-home"))]

    assert sorted(clients) == ["claude-code", "codex", "pi"]


def test_without_overrides_each_client_uses_its_home_location(tmp_path: Path) -> None:
    sources = _by_client({}, tmp_path)

    assert sources["pi"][0] == tmp_path / ".pi" / "agent" / "sessions"
    assert sources["claude-code"][0] == tmp_path / ".claude" / "projects"
    assert sources["codex"][0] == tmp_path / ".codex" / "sessions"


def test_missing_locations_are_reported_not_found(tmp_path: Path) -> None:
    sources = _by_client({}, tmp_path)

    assert [found for _, found in sources.values()] == [False, False, False]


def test_existing_location_is_found_and_others_are_not(tmp_path: Path) -> None:
    (tmp_path / ".codex" / "sessions").mkdir(parents=True)

    sources = _by_client({}, tmp_path)

    assert sources["codex"] == (tmp_path / ".codex" / "sessions", True)
    assert sources["pi"][1] is False
    assert sources["claude-code"][1] is False


def test_location_that_is_a_file_is_not_found(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "projects").write_text("not a directory\n", encoding="utf-8")

    assert _by_client({}, tmp_path)["claude-code"][1] is False


def test_pi_session_dir_override_is_used_as_is(tmp_path: Path) -> None:
    session_dir = tmp_path / "pi-session-dir"
    session_dir.mkdir()

    sources = _by_client({"PI_CODING_AGENT_SESSION_DIR": str(session_dir)}, tmp_path)

    assert sources["pi"] == (session_dir, True)


def test_pi_agent_dir_override_adds_sessions(tmp_path: Path) -> None:
    agent_dir = tmp_path / "pi-agent"

    sources = _by_client({"PI_CODING_AGENT_DIR": str(agent_dir)}, tmp_path)

    assert sources["pi"] == (agent_dir / "sessions", False)


def test_pi_session_dir_override_wins_over_agent_dir_override(tmp_path: Path) -> None:
    environ = {
        "PI_CODING_AGENT_SESSION_DIR": str(tmp_path / "pi-session-dir"),
        "PI_CODING_AGENT_DIR": str(tmp_path / "pi-agent"),
    }

    sources = _by_client(environ, tmp_path)

    assert sources["pi"][0] == tmp_path / "pi-session-dir"


def test_pi_overrides_win_over_an_existing_home_location(tmp_path: Path) -> None:
    (tmp_path / ".pi" / "agent" / "sessions").mkdir(parents=True)
    agent_dir = tmp_path / "pi-agent"

    sources = _by_client({"PI_CODING_AGENT_DIR": str(agent_dir)}, tmp_path)

    assert sources["pi"] == (agent_dir / "sessions", False)


def test_claude_config_dir_override_adds_projects(tmp_path: Path) -> None:
    config = tmp_path / "claude-config"
    (config / "projects").mkdir(parents=True)

    sources = _by_client({"CLAUDE_CONFIG_DIR": str(config)}, tmp_path)

    assert sources["claude-code"] == (config / "projects", True)


def test_codex_home_override_adds_sessions(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    (codex_home / "sessions").mkdir(parents=True)

    sources = _by_client({"CODEX_HOME": str(codex_home)}, tmp_path)

    assert sources["codex"] == (codex_home / "sessions", True)


def test_one_clients_override_leaves_the_others_at_home(tmp_path: Path) -> None:
    sources = _by_client({"CODEX_HOME": str(tmp_path / "codex-home")}, tmp_path)

    assert sources["pi"][0] == tmp_path / ".pi" / "agent" / "sessions"
    assert sources["claude-code"][0] == tmp_path / ".claude" / "projects"


def test_unrelated_environment_variables_change_nothing(tmp_path: Path) -> None:
    environ = {"HOME": "/nonexistent-home", "PI_DIR": "x", "CLAUDE_HOME": "y", "CODEX_DIR": "z"}

    sources = _by_client(environ, tmp_path)

    assert sources["pi"][0] == tmp_path / ".pi" / "agent" / "sessions"
    assert sources["claude-code"][0] == tmp_path / ".claude" / "projects"
    assert sources["codex"][0] == tmp_path / ".codex" / "sessions"


def test_a_leading_tilde_in_an_override_is_the_home_argument(tmp_path: Path) -> None:
    environ = {"PI_CODING_AGENT_DIR": "~/pi-agent", "CODEX_HOME": "~/codex-home"}

    sources = _by_client(environ, tmp_path)

    assert sources["pi"][0] == tmp_path / "pi-agent" / "sessions"
    assert sources["codex"][0] == tmp_path / "codex-home" / "sessions"
