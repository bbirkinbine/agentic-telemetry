"""`agentic-telemetry session-table` with no DIR: the clients' default session locations.

Seam: the CLI calls `default_sources(os.environ, Path.home())`. Every test points
`HOME` at a `tmp_path` directory and clears or sets the four override variables,
so neither the real home directory nor the caller's overrides are ever read.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from claude_code_builders import cc_assistant, cc_user
from codex_builders import codex_meta, codex_rollout_name, codex_turn_context
from session_cli_helpers import CLIENT_PATTERNS, read_rows, run_main, source_status_lines
from session_table_builders import pi_assistant, pi_header, pi_session_file_name, write_jsonl

OVERRIDES = (
    "PI_CODING_AGENT_SESSION_DIR",
    "PI_CODING_AGENT_DIR",
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
)


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    return home


def _pi_session(sessions_dir: Path, session_id: str) -> Path:
    return write_jsonl(
        sessions_dir / "--workspace-alpha-project--" / pi_session_file_name(session_id),
        [pi_header(session_id), pi_assistant()],
    )


def _claude_session(projects_dir: Path, session_id: str) -> Path:
    return write_jsonl(
        projects_dir / "-workspace-beta-project" / f"{session_id}.jsonl",
        [cc_user(session_id=session_id), cc_assistant(session_id=session_id)],
    )


def _codex_session(sessions_dir: Path, session_id: str) -> Path:
    return write_jsonl(
        sessions_dir / "2026" / "10" / "20" / codex_rollout_name(session_id),
        [codex_meta(session_id), codex_turn_context()],
    )


def _out(tmp_path: Path) -> Path:
    out_dir = tmp_path / "data"
    out_dir.mkdir(exist_ok=True)
    return out_dir / "session_table.parquet"


def test_no_dir_reads_each_home_location_that_exists(fake_home: Path, tmp_path: Path) -> None:
    _pi_session(fake_home / ".pi" / "agent" / "sessions", "pi-home-0001")
    _codex_session(fake_home / ".codex" / "sessions", "codex-home-0001")
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out)])

    assert code == 0
    assert set(read_rows(out)) == {("pi", "pi-home-0001"), ("codex", "codex-home-0001")}


def test_summary_names_each_client_as_found_or_not_found(
    fake_home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _pi_session(fake_home / ".pi" / "agent" / "sessions", "pi-home-0001")
    _codex_session(fake_home / ".codex" / "sessions", "codex-home-0001")
    out = _out(tmp_path)

    run_main(["session-table", "--out", str(out)])

    stdout = capsys.readouterr().out
    pi_lines = source_status_lines(stdout, "pi")
    claude_lines = source_status_lines(stdout, "claude-code")
    codex_lines = source_status_lines(stdout, "codex")
    assert pi_lines, "pi named with found / not found"
    assert all("not found" not in line for line in pi_lines)
    assert codex_lines, "codex named with found / not found"
    assert all("not found" not in line for line in codex_lines)
    assert claude_lines, "claude-code named with found / not found"
    assert all("not found" in line for line in claude_lines)


def test_no_dir_applies_each_clients_environment_override(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pi_session(fake_home / ".pi" / "agent" / "sessions", "pi-ignored-0001")
    pi_agent = tmp_path / "pi-agent"
    _pi_session(pi_agent / "sessions", "pi-override-0001")
    claude_config = tmp_path / "claude-config"
    _claude_session(claude_config / "projects", "cc-override-0001")
    codex_home = tmp_path / "codex-home"
    _codex_session(codex_home / "sessions", "codex-override-0001")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(pi_agent))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_config))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out)])

    assert code == 0
    assert set(read_rows(out)) == {
        ("pi", "pi-override-0001"),
        ("claude-code", "cc-override-0001"),
        ("codex", "codex-override-0001"),
    }


def test_pi_session_dir_override_wins_over_pi_agent_dir(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pi_session(tmp_path / "pi-agent" / "sessions", "pi-agent-dir-0001")
    _pi_session(tmp_path / "pi-session-dir", "pi-session-dir-0001")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(tmp_path / "pi-session-dir"))
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out)])

    assert code == 0
    assert set(read_rows(out)) == {("pi", "pi-session-dir-0001")}


def test_no_dir_and_no_default_location_exits_1_and_writes_nothing(
    fake_home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out)])

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert code == 1
    assert list(out.parent.iterdir()) == []
    for client, pattern in CLIENT_PATTERNS.items():
        assert re.search(pattern, combined), client


def test_explicit_dirs_ignore_the_default_locations(fake_home: Path, tmp_path: Path) -> None:
    _pi_session(fake_home / ".pi" / "agent" / "sessions", "pi-home-0001")
    explicit = tmp_path / "explicit"
    _codex_session(explicit, "codex-explicit-0001")
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out), str(explicit)])

    assert code == 0
    assert set(read_rows(out)) == {("codex", "codex-explicit-0001")}


def test_default_locations_that_exist_but_hold_no_transcript_exit_1_and_keep_the_old_table(
    fake_home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (fake_home / ".pi" / "agent" / "sessions").mkdir(parents=True)
    write_jsonl(fake_home / ".claude" / "projects" / "x" / "ledger.jsonl", [{"type": "run"}])
    out = _out(tmp_path)
    out.write_bytes(b"earlier table")

    code = run_main(["session-table", "--out", str(out)])

    assert code == 1
    assert out.read_bytes() == b"earlier table"
    assert "default session directories" in capsys.readouterr().err
