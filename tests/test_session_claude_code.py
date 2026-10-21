"""Claude Code transcripts: message.id dedupe, per-block tool counts, prompts, subagents."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from claude_code_builders import (
    cc_assistant,
    cc_snapshot,
    cc_system,
    cc_text_block,
    cc_tool_result_block,
    cc_tool_use_block,
    cc_usage,
    cc_user,
)
from session_table_builders import BETA_CWD, MODEL, OTHER_MODEL, SENTINEL, Line, ts, write_jsonl

from agentic_telemetry.session_table.claude_code import read_claude_code
from agentic_telemetry.session_table.row import SessionRow


def _write(tmp_path: Path, lines: list[Line], name: str = "cc-main-0001.jsonl") -> Path:
    return write_jsonl(tmp_path / "-workspace-beta-project" / name, lines)


def _row(path: Path) -> SessionRow:
    read = read_claude_code(path)
    assert read.row is not None
    return read.row


def test_main_session_identity_uses_first_cwd_and_last_version_and_branch(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        [
            cc_snapshot(),
            cc_user(version="2.0.1", git_branch="feature-one"),
            cc_assistant(cwd=BETA_CWD + "/docs", version="2.0.2", git_branch="feature-two"),
        ],
    )

    row = _row(path)

    assert row.client == "claude-code"
    assert row.session_id == "cc-main-0001"
    assert row.source_path == str(path)
    assert row.cwd == BETA_CWD
    assert row.project == "beta-project"
    assert row.client_version == "2.0.2"
    assert row.git_branch == "feature-two"
    assert row.is_subagent is False
    assert row.parent_session_id is None
    assert row.provider is None, "Claude Code records no provider"


def test_subagent_is_keyed_by_agent_id_and_names_the_parent_session(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            cc_user(agent_id="cc-agent-0001", session_id="cc-main-0001"),
            cc_assistant(agent_id="cc-agent-0001", session_id="cc-main-0001"),
        ],
        name="cc-main-0001/subagents/agent-cc-agent-0001.jsonl",
    )

    row = _row(path)

    assert row.session_id == "cc-agent-0001"
    assert row.is_subagent is True
    assert row.parent_session_id == "cc-main-0001"


def test_lines_sharing_a_message_id_count_usage_and_one_turn_once(tmp_path: Path) -> None:
    first = cc_usage(tokens_in=100, cache_write=20, cache_read=300, tokens_out=50)
    second = cc_usage(tokens_in=10, cache_write=0, cache_read=400, tokens_out=5)
    path = _write(
        tmp_path,
        [
            cc_user(),
            cc_assistant(message_id="msg-1", block={"type": "thinking"}, usage=first),
            cc_assistant(message_id="msg-1", block=cc_text_block(), usage=first),
            cc_assistant(message_id="msg-1", block=cc_tool_use_block(), usage=first),
            cc_user([cc_tool_result_block()]),
            cc_assistant(message_id="msg-2", usage=second, stop_reason="end_turn"),
        ],
    )

    row = _row(path)

    assert row.assistant_turns == 2
    assert row.tokens_in == 110
    assert row.tokens_cache_write == 20
    assert row.tokens_cache_read == 700
    assert row.tokens_out == 55
    assert row.tokens_reasoning is None, "Claude Code records no reasoning tokens"


def test_stop_reason_is_the_last_non_null_value_per_message(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            cc_assistant(message_id="msg-1", stop_reason=None),
            cc_assistant(message_id="msg-1", stop_reason="tool_use"),
            cc_assistant(message_id="msg-1", stop_reason=None),
            cc_assistant(message_id="msg-2", stop_reason="tool_use"),
            cc_assistant(message_id="msg-3", stop_reason="end_turn"),
        ],
    )

    row = _row(path)

    assert row.stop_reasons is not None
    assert json.loads(row.stop_reasons) == {"tool_use": 2, "end_turn": 1}


def test_assistant_lines_without_a_message_id_are_each_their_own_turn(tmp_path: Path) -> None:
    usage = cc_usage(tokens_in=10, tokens_out=1)
    path = _write(
        tmp_path,
        [cc_assistant(message_id=None, usage=usage), cc_assistant(message_id=None, usage=usage)],
    )

    row = _row(path)

    assert row.assistant_turns == 2
    assert row.tokens_in == 20


def test_tool_calls_and_errors_are_counted_per_block_not_deduped(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            cc_assistant(message_id="msg-1", block=cc_tool_use_block()),
            cc_assistant(message_id="msg-1", block=cc_tool_use_block()),
            cc_user([cc_tool_result_block(is_error=True), cc_tool_result_block(is_error=True)]),
            cc_assistant(message_id="msg-2", block=cc_tool_use_block()),
            cc_user([cc_tool_result_block(is_error=False)]),
        ],
    )

    row = _row(path)

    assert row.tool_calls == 3
    assert row.tool_errors == 2
    assert row.assistant_turns == 2


def test_user_prompts_count_human_text_only(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            cc_user(SENTINEL),
            cc_user([cc_text_block()]),
            cc_user([cc_tool_result_block(), cc_text_block()]),
            cc_user(SENTINEL, isMeta=True),
            cc_user(SENTINEL, isCompactSummary=True),
            cc_user([cc_tool_result_block()]),
            cc_user([cc_tool_result_block(is_error=True)]),
            cc_assistant(),
        ],
    )

    row = _row(path)

    assert row.user_prompts == 3


def test_compactions_count_compact_boundary_system_lines(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            cc_user(),
            cc_system("compact_boundary"),
            cc_system("api_error"),
            cc_system("compact_boundary"),
            cc_assistant(),
        ],
    )

    assert _row(path).compactions == 2


def test_models_and_primary_model_count_deduped_turns(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            cc_assistant(message_id="msg-1", model=MODEL),
            cc_assistant(message_id="msg-2", model=OTHER_MODEL),
            cc_assistant(message_id="msg-2", model=OTHER_MODEL),
            cc_assistant(message_id="msg-2", model=OTHER_MODEL),
            cc_assistant(message_id="msg-3", model=MODEL),
        ],
    )

    row = _row(path)

    assert row.models == (MODEL, OTHER_MODEL)
    assert row.primary_model == MODEL, "2 turns against 1 once msg-2 is deduped"
    assert row.served_models == row.models, "message.model is the response's own model"
    assert row.primary_served_model == MODEL


def test_times_span_the_earliest_and_latest_line(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [cc_snapshot(), cc_user(timestamp=ts(0)), cc_assistant(timestamp=ts(2, 15))],
    )

    row = _row(path)

    assert row.started_at is not None
    assert row.ended_at is not None
    assert datetime.fromisoformat(row.started_at) == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert datetime.fromisoformat(row.ended_at) == datetime(2026, 10, 20, 10, 2, 15, tzinfo=UTC)
    assert row.span_seconds == 135.0


def test_main_file_with_two_session_ids_takes_the_last_and_warns(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [cc_user(session_id="cc-first-0001"), cc_assistant(session_id="cc-second-0001")],
    )

    read = read_claude_code(path)

    assert read.row is not None
    assert read.row.session_id == "cc-second-0001"
    assert len(read.warnings) == 1
    assert str(path) in read.warnings[0]


def test_non_utf8_line_warns_and_the_rest_still_counts(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [cc_user(), b"\xff\xfe " + SENTINEL.encode(), cc_assistant(stop_reason="end_turn")],
    )

    read = read_claude_code(path)

    assert read.row is not None
    assert read.row.assistant_turns == 1
    assert read.line_count == 3
    assert [warning.startswith(f"{path}:2:") for warning in read.warnings] == [True]
    assert SENTINEL not in read.warnings[0]


def test_mistyped_fields_become_nulls_without_raising(tmp_path: Path) -> None:
    odd: dict[str, object] = {"cwd": None, "version": 3, "git_branch": ["main"]}
    message_not_an_object = cc_assistant(message_id="msg-odd", **odd)
    message_not_an_object["message"] = "not-an-object"
    path = _write(
        tmp_path,
        [
            cc_user(**odd),
            cc_assistant(model=None, usage={"input_tokens": "lots", "output_tokens": 4}, **odd),
            message_not_an_object,
        ],
    )

    row = _row(path)

    assert row.cwd is None
    assert row.project is None
    assert row.client_version is None
    assert row.git_branch is None
    assert row.models == ()
    assert row.tokens_in is None
    assert row.tokens_out == 4


def test_a_stray_sidechain_line_does_not_rekey_a_main_session(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [cc_user(), cc_assistant(agent_id="cc-agent-0001", message_id="msg-x"), cc_assistant()],
    )

    row = _row(path)

    assert row.session_id == "cc-main-0001"
    assert row.is_subagent is False
