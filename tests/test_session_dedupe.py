"""Duplicate (client, session_id) rows: the longer file wins, a warning names both, no raise.

`merge_sessions(reads)` takes `SessionRead(row, line_count, warnings)` values and
returns `(rows, duplicates_resolved, warnings)`. `SessionRead` lives beside
`SessionRow` in `session_table.row`.
"""

from __future__ import annotations

from pathlib import Path

from session_table_builders import (
    pi_assistant,
    pi_header,
    pi_session_file_name,
    pi_user,
    ts,
    write_jsonl,
)
from session_tree_builder import session_row_values

from agentic_telemetry.session_table.build import build_session_table
from agentic_telemetry.session_table.dedupe import merge_sessions
from agentic_telemetry.session_table.row import SessionRead, SessionRow


def _read(
    source_path: str, line_count: int, *, client: str = "pi", session_id: str = "s-1"
) -> SessionRead:
    row = SessionRow(
        **session_row_values(client=client, session_id=session_id, source_path=source_path)
    )
    return SessionRead(row=row, line_count=line_count, warnings=[])


def test_the_file_with_more_lines_wins_whatever_the_input_order() -> None:
    live = _read("work/live/session.jsonl", 12)
    stale = _read("inbox/copy/session.jsonl", 7)

    for reads in ([live, stale], [stale, live]):
        rows, resolved, _ = merge_sessions(reads)

        assert [row.source_path for row in rows] == ["work/live/session.jsonl"]
        assert resolved == 1


def test_equal_line_counts_keep_the_path_that_sorts_first() -> None:
    first = _read("a-dir/session.jsonl", 9)
    second = _read("b-dir/session.jsonl", 9)

    for reads in ([first, second], [second, first]):
        rows, resolved, _ = merge_sessions(reads)

        assert [row.source_path for row in rows] == ["a-dir/session.jsonl"]
        assert resolved == 1


def test_the_duplicate_warning_names_both_paths() -> None:
    rows, _, warnings = merge_sessions(
        [_read("work/live/session.jsonl", 12), _read("inbox/copy/session.jsonl", 7)]
    )

    assert len(rows) == 1
    assert len(warnings) == 1
    assert "work/live/session.jsonl" in warnings[0]
    assert "inbox/copy/session.jsonl" in warnings[0]


def test_the_same_session_id_under_two_clients_is_not_a_duplicate() -> None:
    rows, resolved, warnings = merge_sessions(
        [_read("p.jsonl", 3, client="pi"), _read("c.jsonl", 3, client="codex")]
    )

    assert sorted(row.client for row in rows) == ["codex", "pi"]
    assert resolved == 0
    assert warnings == []


def test_rows_come_back_sorted_by_client_then_session_id() -> None:
    reads = [
        _read("1.jsonl", 1, client="pi", session_id="b"),
        _read("2.jsonl", 1, client="codex", session_id="z"),
        _read("3.jsonl", 1, client="pi", session_id="a"),
        _read("4.jsonl", 1, client="claude-code", session_id="m"),
    ]

    rows, _, _ = merge_sessions(reads)

    assert [(row.client, row.session_id) for row in rows] == [
        ("claude-code", "m"),
        ("codex", "z"),
        ("pi", "a"),
        ("pi", "b"),
    ]


def test_a_stale_inbox_copy_is_resolved_end_to_end_without_raising(tmp_path: Path) -> None:
    name = pi_session_file_name("pi-shared-0001")
    prefix = [pi_header("pi-shared-0001"), pi_user(), pi_assistant(timestamp=ts(1))]
    live = write_jsonl(
        tmp_path / "work" / name, [*prefix, pi_assistant(timestamp=ts(2)), pi_user()]
    )
    write_jsonl(tmp_path / "inbox" / "laptop.example" / name, prefix)

    rows, _ = build_session_table([tmp_path / "work", tmp_path / "inbox"])

    assert [(row.client, row.session_id) for row in rows] == [("pi", "pi-shared-0001")]
    assert rows[0].source_path == str(live)
    assert rows[0].assistant_turns == 2
