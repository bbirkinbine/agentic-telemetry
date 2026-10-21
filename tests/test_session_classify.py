"""Classify a transcript by its content: Pi, Codex, Claude Code, or unrecognized.

`classify_transcript(path)` returns the client name (`"pi"`, `"codex"`,
`"claude-code"`) or None. Comparing against plain strings works whether
`Client` is a `Literal` alias or a `StrEnum`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from claude_code_builders import cc_assistant, cc_snapshot, cc_system, cc_user
from codex_builders import codex_meta, codex_response_item, codex_turn_context
from session_table_builders import (
    SENTINEL,
    Line,
    pi_assistant,
    pi_header,
    pi_user,
    write_jsonl,
    write_jsonl_zst,
)

from agentic_telemetry.session_table.classify import classify_transcript


def _write(tmp_path: Path, lines: list[Line], name: str = "t.jsonl") -> Path:
    return write_jsonl(tmp_path / name, lines)


def test_pi_header_as_first_line_is_pi(tmp_path: Path) -> None:
    path = _write(tmp_path, [pi_header(), pi_user(), pi_assistant()])

    assert classify_transcript(path) == "pi"


def test_pi_header_after_an_unparseable_first_line_is_pi(tmp_path: Path) -> None:
    path = _write(tmp_path, ["{broken " + SENTINEL, pi_header(), pi_assistant()])

    assert classify_transcript(path) == "pi"


def test_session_line_without_a_version_is_not_pi(tmp_path: Path) -> None:
    path = _write(tmp_path, [pi_header(drop=("version",)), pi_assistant()])

    assert classify_transcript(path) is None


def test_pi_header_that_is_not_the_first_parseable_line_is_not_pi(tmp_path: Path) -> None:
    path = _write(tmp_path, [pi_user(), pi_header(), pi_assistant()])

    assert classify_transcript(path) is None


def _codex_filler(count: int) -> list[Line]:
    return [codex_response_item("reasoning", summary=[]) for _ in range(count)]


def test_codex_session_meta_on_line_1_is_codex(tmp_path: Path) -> None:
    path = _write(tmp_path, [codex_meta(), codex_turn_context()])

    assert classify_transcript(path) == "codex"


def test_codex_session_meta_on_line_20_is_codex(tmp_path: Path) -> None:
    path = _write(tmp_path, [*_codex_filler(19), codex_meta()])

    assert classify_transcript(path) == "codex"


def test_codex_session_meta_on_line_21_is_unrecognized(tmp_path: Path) -> None:
    path = _write(tmp_path, [*_codex_filler(20), codex_meta()])

    assert classify_transcript(path) is None


def test_compressed_codex_rollout_is_codex(tmp_path: Path) -> None:
    path = write_jsonl_zst(tmp_path / "rollout.jsonl.zst", [codex_meta(), codex_turn_context()])

    assert classify_transcript(path) == "codex"


def _claude_filler(count: int) -> list[Line]:
    return [cc_snapshot() for _ in range(count)]


@pytest.mark.parametrize(
    "line",
    [pytest.param(cc_user(), id="user"), pytest.param(cc_assistant(), id="assistant")],
)
def test_user_or_assistant_line_with_session_id_is_claude_code(
    tmp_path: Path, line: dict[str, object]
) -> None:
    path = _write(tmp_path, [cc_snapshot(), line])

    assert classify_transcript(path) == "claude-code"


def test_claude_code_line_on_line_50_is_claude_code(tmp_path: Path) -> None:
    path = _write(tmp_path, [*_claude_filler(49), cc_user()])

    assert classify_transcript(path) == "claude-code"


def test_claude_code_line_on_line_51_is_unrecognized(tmp_path: Path) -> None:
    path = _write(tmp_path, [*_claude_filler(50), cc_user()])

    assert classify_transcript(path) is None


def test_system_line_with_session_id_alone_is_not_claude_code(tmp_path: Path) -> None:
    path = _write(tmp_path, [cc_snapshot(), cc_system("compact_boundary")])

    assert classify_transcript(path) is None


def test_user_line_without_session_id_is_not_claude_code(tmp_path: Path) -> None:
    line = cc_user()
    del line["sessionId"]
    path = _write(tmp_path, [line])

    assert classify_transcript(path) is None


def test_subagent_file_is_claude_code(tmp_path: Path) -> None:
    path = _write(tmp_path, [cc_user(agent_id="agent-0001")], name="agent-agent-0001.jsonl")

    assert classify_transcript(path) == "claude-code"


@pytest.mark.parametrize(
    ("name", "lines"),
    [
        pytest.param(
            "history.jsonl",
            [{"session_id": "codex-main-0001", "ts": 1760954400, "text": SENTINEL}],
            id="codex-history",
        ),
        pytest.param(
            "session_index.jsonl",
            [{"id": "codex-main-0001", "thread_name": SENTINEL, "updated_at": "x"}],
            id="codex-session-index",
        ),
        pytest.param(
            "ledger.jsonl",
            [{"type": "run", "run": "run-0001"}, {"type": "review", "run": "run-0001"}],
            id="delegate-ledger",
        ),
        pytest.param("empty.jsonl", [], id="empty"),
        pytest.param("junk.jsonl", [b"\xff\xfe\xfd", "{broken", json.dumps([1, 2])], id="junk"),
    ],
)
def test_other_jsonl_files_are_unrecognized(tmp_path: Path, name: str, lines: list[Line]) -> None:
    path = _write(tmp_path, lines, name=name)

    assert classify_transcript(path) is None
