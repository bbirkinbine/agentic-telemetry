"""`agentic-telemetry session-table` with explicit DIRs, end to end through `main(argv)`."""

from __future__ import annotations

from pathlib import Path

import pytest
from session_cli_helpers import CLIENT_PATTERNS, read_rows, run_main, summary_has
from session_table_builders import (
    ALPHA_CWD,
    BETA_CWD,
    GAMMA_CWD,
    MODEL,
    SENTINEL,
    pi_assistant,
    pi_header,
    write_jsonl,
)
from session_tree_builder import SessionTree


def _out(tmp_path: Path) -> Path:
    out_dir = tmp_path / "data"
    out_dir.mkdir(exist_ok=True)
    return out_dir / "session_table.parquet"


def test_writes_one_row_per_client_session(session_tree: SessionTree, tmp_path: Path) -> None:
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out), str(session_tree.root)])

    assert code == 0
    rows = read_rows(out)
    assert set(rows) == session_tree.expected_keys
    assert ("pi", session_tree.artifact_session_id) not in rows


def test_rows_carry_run_id_dedupe_winner_and_compressed_usage(
    session_tree: SessionTree, tmp_path: Path
) -> None:
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out), str(session_tree.root)])

    assert code == 0
    rows = read_rows(out)
    assert rows[("pi", session_tree.delegate_session_id)]["run_id"] == session_tree.delegate_run_id
    assert [key for key, row in rows.items() if row["run_id"] is not None] == [
        ("pi", session_tree.delegate_session_id)
    ]
    assert rows[("pi", "pi-main-0001")]["source_path"] == str(session_tree.live_pi_path)
    assert rows[("codex", "codex-main-0001")]["tokens_in"] == 400
    assert rows[("codex", "codex-child-0001")]["parent_session_id"] == "codex-main-0001"
    assert rows[("claude-code", "cc-agent-0001")]["parent_session_id"] == "cc-main-0001"
    assert rows[("pi", "pi-child-0001")]["parent_session_id"] == "pi-main-0001"


def test_summary_reports_every_count(
    session_tree: SessionTree, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out), str(session_tree.root)])

    stdout = capsys.readouterr().out
    assert code == 0
    assert summary_has(stdout, "scanned", session_tree.files_scanned), "files scanned"
    for client, count in session_tree.sessions_per_client.items():
        assert summary_has(stdout, CLIENT_PATTERNS[client], count), f"{client} sessions"
    assert summary_has(stdout, "unrecognized", session_tree.unrecognized), "unrecognized"
    assert summary_has(stdout, "token", session_tree.sessions_with_tokens), "with tokens"
    assert summary_has(stdout, "duplicate", session_tree.duplicates_resolved), "duplicates"
    assert f"wrote {len(session_tree.expected_keys)} rows to {out}" in stdout


def test_summary_prints_every_warning_with_its_location(
    session_tree: SessionTree, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _out(tmp_path)

    run_main(["session-table", "--out", str(out), str(session_tree.root)])

    stdout = capsys.readouterr().out
    assert session_tree.malformed_location in stdout
    assert session_tree.non_utf8_location in stdout
    live, stale = str(session_tree.live_pi_path), str(session_tree.stale_pi_path)
    assert any(live in line and stale in line for line in stdout.splitlines())


def test_output_never_echoes_content_cwd_or_model(
    session_tree: SessionTree, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out), str(session_tree.root)])

    captured = capsys.readouterr()
    assert code == 0
    for text in (captured.out, captured.err):
        for forbidden in (SENTINEL, ALPHA_CWD, BETA_CWD, GAMMA_CWD, MODEL):
            assert forbidden not in text, forbidden


def test_transcript_without_a_session_id_warns_and_writes_no_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sessions = tmp_path / "sessions"
    write_jsonl(sessions / "x_pi-good-0001.jsonl", [pi_header("pi-good-0001"), pi_assistant()])
    no_id = write_jsonl(sessions / "x_pi-no-id.jsonl", [pi_header(drop=("id",)), pi_assistant()])
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out), str(sessions)])

    assert code == 0
    assert set(read_rows(out)) == {("pi", "pi-good-0001")}
    assert str(no_id) in capsys.readouterr().out


def test_existing_output_file_is_rebuilt(session_tree: SessionTree, tmp_path: Path) -> None:
    out = _out(tmp_path)
    out.write_bytes(b"stale content from an earlier build")

    code = run_main(["session-table", "--out", str(out), str(session_tree.root)])

    assert code == 0
    assert len(read_rows(out)) == len(session_tree.expected_keys)


def test_directory_without_a_transcript_exits_1_naming_it_and_writes_nothing(
    session_tree: SessionTree, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "no-transcripts-here"
    write_jsonl(empty / "history.jsonl", [{"session_id": "s", "ts": 1}])
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out), str(session_tree.root), str(empty)])

    assert code == 1
    assert str(empty) in capsys.readouterr().err
    assert list(out.parent.iterdir()) == []


def test_nonexistent_input_directory_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out), str(tmp_path / "missing-dir")])

    assert code == 2
    assert "does not exist" in capsys.readouterr().err
    assert not out.exists()


def test_out_without_parquet_suffix_is_a_usage_error(
    session_tree: SessionTree, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "session_table.csv"

    code = run_main(["session-table", "--out", str(out), str(session_tree.root)])

    assert code == 2
    assert "must end in .parquet" in capsys.readouterr().err
    assert not out.exists()


def test_missing_out_option_is_a_usage_error(
    session_tree: SessionTree, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_main(["session-table", str(session_tree.root)])

    err = capsys.readouterr().err
    assert code == 2
    assert "--out" in err
    assert "required" in err


def test_session_table_help_states_intent_and_default_locations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_main(["session-table", "--help"])

    help_text = " ".join(capsys.readouterr().out.split())
    assert code == 0
    assert "--out" in help_text
    for name in (
        "PI_CODING_AGENT_SESSION_DIR",
        "PI_CODING_AGENT_DIR",
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
        "~/.pi/agent/sessions",
        "~/.claude/projects",
        "~/.codex/sessions",
    ):
        assert name in help_text, name


def test_top_level_help_lists_both_commands(capsys: pytest.CaptureFixture[str]) -> None:
    code = run_main(["--help"])

    stdout = capsys.readouterr().out
    assert code == 0
    assert "session-table" in stdout
    assert "run-table" in stdout


def test_unreadable_compressed_file_is_warned_in_the_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sessions = tmp_path / "sessions"
    write_jsonl(sessions / "x_pi-good-0001.jsonl", [pi_header("pi-good-0001"), pi_assistant()])
    corrupt = sessions / "rollout-bad.jsonl.zst"
    corrupt.write_bytes(b"not zstandard data")
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out), str(sessions)])

    stdout = capsys.readouterr().out
    assert code == 0
    assert f"{corrupt}: skipped, could not be read (ZstdError)" in stdout
    assert summary_has(stdout, "unrecognized", 0)


def test_explicit_dir_whose_transcripts_yield_no_row_exits_1_naming_the_given_dirs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sessions = tmp_path / "sessions"
    write_jsonl(sessions / "x_pi-no-id.jsonl", [pi_header(drop=("id",)), pi_assistant()])
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out), str(sessions)])

    err = capsys.readouterr().err
    assert code == 1
    assert "given directories" in err
    assert "default" not in err
    assert not out.exists()


def test_dir_failing_for_an_unreadable_file_prints_that_cause(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sessions = tmp_path / "sessions"
    corrupt = sessions / "rollout-bad.jsonl.zst"
    sessions.mkdir()
    corrupt.write_bytes(b"not zstandard data")
    out = _out(tmp_path)

    code = run_main(["session-table", "--out", str(out), str(sessions)])

    err = capsys.readouterr().err
    assert code == 1
    assert f"{corrupt}: skipped, could not be read" in err
    assert f"no session transcript under {sessions}" in err
    assert not out.exists()
