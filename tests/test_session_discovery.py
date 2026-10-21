"""Discovery: which files are scanned, which are transcripts, and the delegate run ID.

`find_transcripts(dirs)` returns a `Discovery` whose `transcripts` items carry
`path` and `client`, and whose `files_scanned` and `unrecognized` are counts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from codex_builders import codex_meta, codex_rollout_name
from session_table_builders import SENTINEL, pi_assistant, pi_header, write_jsonl, write_jsonl_zst
from session_tree_builder import SessionTree

from agentic_telemetry.session_table.discovery import (
    NoTranscriptError,
    delegate_run_id,
    find_transcripts,
)


def _pi_file(directory: Path, session_id: str = "pi-main-0001") -> Path:
    return write_jsonl(directory / f"x_{session_id}.jsonl", [pi_header(session_id), pi_assistant()])


def test_finds_plain_and_compressed_transcripts_with_their_client(tmp_path: Path) -> None:
    pi = _pi_file(tmp_path / "pi" / "--workspace-alpha-project--")
    codex = write_jsonl_zst(
        tmp_path / "codex" / "2026" / "10" / "20" / (codex_rollout_name("c1") + ".zst"),
        [codex_meta("c1")],
    )

    found = find_transcripts([tmp_path])

    assert {(item.path, item.client) for item in found.transcripts} == {
        (pi, "pi"),
        (codex, "codex"),
    }
    assert found.files_scanned == 2
    assert found.unrecognized == 0


@pytest.mark.parametrize(
    "skipped", [".git", "node_modules", ".venv", "__pycache__", "subagent-artifacts"]
)
def test_transcripts_under_skipped_directories_are_not_scanned(
    tmp_path: Path, skipped: str
) -> None:
    kept = _pi_file(tmp_path / "sessions")
    _pi_file(tmp_path / "sessions" / skipped / "nested", "pi-hidden-0001")

    found = find_transcripts([tmp_path])

    assert [item.path for item in found.transcripts] == [kept]
    assert found.files_scanned == 1


def test_only_jsonl_and_jsonl_zst_files_are_read(tmp_path: Path) -> None:
    kept = _pi_file(tmp_path)
    (tmp_path / "auth.json").write_text(json.dumps({"token": SENTINEL}), encoding="utf-8")
    write_jsonl(tmp_path / "copy.json", [pi_header("pi-json-0001")])
    write_jsonl(tmp_path / "notes.jsonl.bak", [pi_header("pi-bak-0001")])
    (tmp_path / "state.sqlite").write_bytes(b"SQLite format 3\x00")

    found = find_transcripts([tmp_path])

    assert [item.path for item in found.transcripts] == [kept]
    assert found.files_scanned == 1
    assert found.unrecognized == 0


def test_unrecognized_jsonl_files_are_counted_and_skipped(tmp_path: Path) -> None:
    kept = _pi_file(tmp_path / "sessions")
    write_jsonl(tmp_path / "codex-home" / "history.jsonl", [{"session_id": "s", "ts": 1}])
    write_jsonl(tmp_path / "codex-home" / "session_index.jsonl", [{"id": "s"}])
    write_jsonl(tmp_path / "proj" / ".agentic" / "delegate" / "ledger.jsonl", [{"type": "run"}])

    found = find_transcripts([tmp_path])

    assert [item.path for item in found.transcripts] == [kept]
    assert found.files_scanned == 4
    assert found.unrecognized == 3


def test_overlapping_directories_return_each_transcript_once(tmp_path: Path) -> None:
    pi = _pi_file(tmp_path / "a" / "b")

    found = find_transcripts([tmp_path, tmp_path / "a", tmp_path / "a" / "b"])

    assert [item.path for item in found.transcripts] == [pi]


def test_transcripts_in_one_directory_come_back_in_sorted_path_order(tmp_path: Path) -> None:
    for session_id in ("pi-c", "pi-a", "pi-b"):
        _pi_file(tmp_path / session_id, session_id)

    found = find_transcripts([tmp_path])

    paths = [item.path for item in found.transcripts]
    assert paths == sorted(paths)
    assert len(paths) == 3


def test_relative_directory_yields_absolute_transcript_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pi_file(tmp_path / "sessions")
    monkeypatch.chdir(tmp_path / "sessions")

    (item,) = find_transcripts([Path()]).transcripts

    assert item.path.is_absolute()


def test_symlinked_directories_are_not_followed(tmp_path: Path) -> None:
    kept = _pi_file(tmp_path / "walked")
    elsewhere = tmp_path / "elsewhere"
    _pi_file(elsewhere, "pi-linked-0001")
    (tmp_path / "walked" / "link").symlink_to(elsewhere, target_is_directory=True)

    found = find_transcripts([tmp_path / "walked"])

    assert [item.path for item in found.transcripts] == [kept]


def test_directory_with_no_transcript_raises_naming_it(tmp_path: Path) -> None:
    empty = tmp_path / "nothing-here"
    empty.mkdir()

    with pytest.raises(NoTranscriptError) as excinfo:
        find_transcripts([empty])

    assert str(empty) in str(excinfo.value)


def test_directory_holding_only_unrecognized_files_raises_naming_it(tmp_path: Path) -> None:
    noise = tmp_path / "codex-home"
    write_jsonl(noise / "history.jsonl", [{"session_id": "s", "ts": 1}])

    with pytest.raises(NoTranscriptError) as excinfo:
        find_transcripts([noise])

    assert str(noise) in str(excinfo.value)


def test_one_empty_directory_fails_even_when_another_has_transcripts(tmp_path: Path) -> None:
    _pi_file(tmp_path / "good")
    empty = tmp_path / "empty-dir"
    empty.mkdir()

    with pytest.raises(NoTranscriptError) as excinfo:
        find_transcripts([tmp_path / "good", empty])

    assert str(empty) in str(excinfo.value)


def test_session_tree_counts(session_tree: SessionTree) -> None:
    found = find_transcripts([session_tree.root])

    assert found.files_scanned == session_tree.files_scanned
    assert found.unrecognized == session_tree.unrecognized
    assert len(found.transcripts) == len(session_tree.expected_keys) + 1  # + the stale copy


def test_delegate_run_id_is_the_run_directory_above_session(tmp_path: Path) -> None:
    path = tmp_path / "delegate" / "results" / "run-0042" / "session" / "s.jsonl"

    assert delegate_run_id(path) == "run-0042"


@pytest.mark.parametrize(
    "relative",
    [
        pytest.param("results/run-0042/logs/s.jsonl", id="not-in-session"),
        pytest.param("archive/run-0042/session/s.jsonl", id="not-under-results"),
        pytest.param("pi/--workspace-alpha-project--/x_s.jsonl", id="client-session-dir"),
    ],
)
def test_delegate_run_id_is_none_outside_results_run_session(tmp_path: Path, relative: str) -> None:
    assert delegate_run_id(tmp_path / relative) is None


def test_corrupt_compressed_file_warns_naming_it_and_is_not_unrecognized(tmp_path: Path) -> None:
    kept = _pi_file(tmp_path)
    corrupt = tmp_path / (codex_rollout_name("c-bad") + ".zst")
    corrupt.write_bytes(b"not zstandard data " + SENTINEL.encode())

    found = find_transcripts([tmp_path])

    assert [item.path for item in found.transcripts] == [kept]
    assert found.unrecognized == 0
    assert len(found.warnings) == 1
    assert found.warnings[0].startswith(f"{corrupt}: skipped, could not be read")
    assert SENTINEL not in found.warnings[0]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a mode-000 file")
def test_unreadable_file_warns_naming_it(tmp_path: Path) -> None:
    kept = _pi_file(tmp_path)
    locked = _pi_file(tmp_path / "locked", "pi-locked-0001")
    locked.chmod(0)
    try:
        found = find_transcripts([tmp_path])
    finally:
        locked.chmod(0o600)

    assert [item.path for item in found.transcripts] == [kept]
    assert [warning.split(": ")[0] for warning in found.warnings] == [str(locked)]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can list a mode-000 directory")
def test_unlistable_directory_warns_naming_it_and_the_rest_is_found(tmp_path: Path) -> None:
    kept = _pi_file(tmp_path / "readable")
    locked = tmp_path / "locked"
    _pi_file(locked, "pi-locked-0001")
    locked.chmod(0)
    try:
        found = find_transcripts([tmp_path])
    finally:
        locked.chmod(0o700)

    assert [item.path for item in found.transcripts] == [kept]
    assert len(found.warnings) == 1
    assert found.warnings[0].startswith(f"{locked}: skipped, could not be read")


def test_symlink_loop_warns_and_never_raises(tmp_path: Path) -> None:
    kept = _pi_file(tmp_path)
    loop = tmp_path / "loop.jsonl"
    loop.symlink_to(loop)

    found = find_transcripts([tmp_path])

    assert [item.path for item in found.transcripts] == [kept]
    assert [warning.split(": ")[0] for warning in found.warnings] == [str(loop)]
