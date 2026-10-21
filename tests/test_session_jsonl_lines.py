"""Streaming JSONL reader for transcripts: plain and `.jsonl.zst`, bad lines warned and skipped.

Seam chosen for the plan's `jsonl_lines` module: `JsonlLines(path)` is iterable
and yields `(line_no, JsonObject)` with 1-based physical line numbers. After
iteration, `warnings` holds one `path:line: <reason>` string per skipped line and
`line_count` holds the number of physical lines read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from session_table_builders import SENTINEL, write_jsonl, write_jsonl_zst

from agentic_telemetry.session_table.jsonl_lines import JsonlLines

FIRST = {"type": "first", "n": 1}
SECOND = {"type": "second", "n": 2}
THIRD = {"type": "third", "n": 3}


def test_yields_each_object_with_its_line_number(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "a.jsonl", [FIRST, SECOND, THIRD])

    lines = JsonlLines(path)

    assert list(lines) == [(1, FIRST), (2, SECOND), (3, THIRD)]
    assert lines.warnings == []
    assert lines.line_count == 3


def test_reads_a_zstandard_compressed_file_like_a_plain_one(tmp_path: Path) -> None:
    path = write_jsonl_zst(tmp_path / "rollout.jsonl.zst", [FIRST, SECOND, THIRD])

    lines = JsonlLines(path)

    assert list(lines) == [(1, FIRST), (2, SECOND), (3, THIRD)]
    assert lines.line_count == 3


def test_last_line_without_a_trailing_newline_is_still_read(tmp_path: Path) -> None:
    path = tmp_path / "truncated.jsonl"
    path.write_text(json.dumps(FIRST) + "\n" + json.dumps(SECOND), encoding="utf-8")

    assert list(JsonlLines(path)) == [(1, FIRST), (2, SECOND)]


def test_blank_lines_are_skipped_silently_and_keep_physical_numbering(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "a.jsonl", [FIRST, "", "   ", SECOND])

    lines = JsonlLines(path)

    assert list(lines) == [(1, FIRST), (4, SECOND)]
    assert lines.warnings == []


@pytest.mark.parametrize(
    "bad_line",
    [
        pytest.param('{"type": "message", "text": "' + SENTINEL + '"', id="malformed-json"),
        pytest.param(json.dumps([SENTINEL, "list"]), id="array"),
        pytest.param(json.dumps(SENTINEL), id="string"),
        pytest.param("42", id="number"),
    ],
)
def test_unusable_line_warns_with_path_and_line_and_never_echoes_text(
    tmp_path: Path, bad_line: str
) -> None:
    path = write_jsonl(tmp_path / "a.jsonl", [FIRST, bad_line, SECOND])

    lines = JsonlLines(path)
    records = list(lines)

    assert records == [(1, FIRST), (3, SECOND)]
    assert len(lines.warnings) == 1
    assert lines.warnings[0].startswith(f"{path}:2:")
    assert SENTINEL not in lines.warnings[0]


def test_non_utf8_line_warns_with_path_and_line_and_never_echoes_bytes(tmp_path: Path) -> None:
    bad = b"\xff\xfe " + SENTINEL.encode() + b" \xc3"
    path = write_jsonl(tmp_path / "a.jsonl", [FIRST, bad, SECOND])

    lines = JsonlLines(path)
    records = list(lines)

    assert records == [(1, FIRST), (3, SECOND)]
    assert len(lines.warnings) == 1
    assert lines.warnings[0].startswith(f"{path}:2:")
    assert SENTINEL not in lines.warnings[0]


def test_bad_lines_in_a_compressed_file_name_the_compressed_path(tmp_path: Path) -> None:
    path = write_jsonl_zst(tmp_path / "rollout.jsonl.zst", [FIRST, "{broken", SECOND])

    lines = JsonlLines(path)
    records = list(lines)

    assert records == [(1, FIRST), (3, SECOND)]
    assert len(lines.warnings) == 1
    assert lines.warnings[0].startswith(f"{path}:2:")


def test_line_count_includes_lines_that_were_skipped_with_a_warning(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "a.jsonl", [FIRST, "{broken", b"\xff\xfe", SECOND])

    lines = JsonlLines(path)
    list(lines)

    assert lines.line_count == 4
    assert len(lines.warnings) == 2


@pytest.mark.parametrize(
    "bad_line",
    [
        pytest.param('{"n": ' + "9" * 5000 + "}", id="integer-over-the-digit-limit"),
        pytest.param("[" * 100_000 + "]" * 100_000, id="nesting-past-the-recursion-limit"),
    ],
)
def test_json_python_refuses_warns_and_never_raises(tmp_path: Path, bad_line: str) -> None:
    path = write_jsonl(tmp_path / "a.jsonl", [FIRST, bad_line, SECOND])

    lines = JsonlLines(path)

    assert list(lines) == [(1, FIRST), (3, SECOND)]
    assert len(lines.warnings) == 1
    assert lines.warnings[0].startswith(f"{path}:2:")
