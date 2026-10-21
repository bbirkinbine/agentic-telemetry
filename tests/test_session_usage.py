"""Session usage: token totals and assistant turns from Pi transcripts."""

from __future__ import annotations

import re
from pathlib import Path

from run_table_builders import (
    assistant_message,
    other_role_message,
    session_header,
    write_session,
)

from agentic_telemetry.run_table.session_usage import read_session_usage


def test_sums_assistant_usage_across_every_transcript_file(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "run-handoff"
    write_session(
        run_dir,
        [
            session_header("s-one"),
            assistant_message(tokens_in=100, tokens_out=20, cache_read=5, reasoning=3),
            assistant_message(tokens_in=200, tokens_out=30, cache_read=7, reasoning=4),
        ],
        name="0001.jsonl",
    )
    write_session(
        run_dir,
        [
            session_header("s-two"),
            assistant_message(tokens_in=1000, tokens_out=300, cache_read=11, reasoning=13),
        ],
        name="0002.jsonl",
    )

    usage = read_session_usage(run_dir / "session")

    assert usage is not None
    assert usage.tokens_in == 1300
    assert usage.tokens_out == 350
    assert usage.tokens_cache_read == 23
    assert usage.tokens_reasoning == 20
    assert usage.assistant_turns == 3


def test_cache_write_and_total_tokens_are_not_added_to_any_column(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "run-handoff"
    write_session(
        run_dir,
        [session_header(), assistant_message(tokens_in=10, tokens_out=2, cache_write=9000)],
    )

    usage = read_session_usage(run_dir / "session")

    assert usage is not None
    assert (usage.tokens_in, usage.tokens_out, usage.tokens_cache_read) == (10, 2, 0)
    assert usage.tokens_reasoning == 0


def test_session_header_and_non_assistant_lines_are_ignored(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "run-handoff"
    write_session(
        run_dir,
        [
            session_header(),
            {"type": "model_change", "id": "c1", "provider": "example", "modelId": "example"},
            {"type": "thinking_level_change", "id": "c2", "thinkingLevel": "medium"},
            other_role_message("user"),
            assistant_message(tokens_in=40, tokens_out=8),
            other_role_message("toolResult"),
            assistant_message(tokens_in=60, tokens_out=12),
        ],
    )

    usage = read_session_usage(run_dir / "session")

    assert usage is not None
    assert usage.tokens_in == 100
    assert usage.tokens_out == 20
    assert usage.assistant_turns == 2
    assert list(usage.warnings) == []


def test_missing_session_directory_gives_none(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "run-per-file"
    run_dir.mkdir(parents=True)

    assert read_session_usage(run_dir / "session") is None


def test_session_directory_without_transcripts_gives_none(tmp_path: Path) -> None:
    session_dir = tmp_path / "results" / "run-per-file" / "session"
    session_dir.mkdir(parents=True)
    (session_dir / "notes.txt").write_text("not a transcript\n", encoding="utf-8")

    assert read_session_usage(session_dir) is None


def test_malformed_transcript_lines_give_one_warning_per_file(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "run-handoff"
    transcript = write_session(
        run_dir,
        [
            session_header(),
            assistant_message(tokens_in=10, tokens_out=1),
            "{truncated line",
            "also not json",
            assistant_message(tokens_in=20, tokens_out=2),
        ],
    )

    usage = read_session_usage(run_dir / "session")

    assert usage is not None
    assert usage.tokens_in == 30
    assert usage.assistant_turns == 2
    assert len(usage.warnings) == 1
    warning = usage.warnings[0]
    assert f"{transcript}:3" in warning, "names the file and its first bad line"
    assert re.search(r"(?<![\w.:/-])2(?![\w.:/-])", warning), "states the bad-line count"


def test_transcript_cut_inside_a_multibyte_character_still_counts(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "run-handoff"
    transcript = write_session(
        run_dir, [session_header(), assistant_message(tokens_in=10, tokens_out=1)]
    )
    with transcript.open("ab") as handle:
        handle.write(b'{"type": "message", "message": {"role": "assistant", "text": "\xe2\x82')

    usage = read_session_usage(run_dir / "session")

    assert usage is not None
    assert usage.tokens_in == 10
    assert usage.assistant_turns == 1
    assert len(usage.warnings) == 1
    assert f"{transcript}:3" in usage.warnings[0]
