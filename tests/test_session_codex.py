"""Codex rollouts: cumulative token_count usage, turns, tools, prompts, compactions, lineage."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from codex_builders import (
    codex_compacted,
    codex_item_completed,
    codex_line,
    codex_meta,
    codex_response_item,
    codex_rollout_name,
    codex_task_complete,
    codex_token_count,
    codex_token_count_without_info,
    codex_turn_context,
    codex_user_response_item,
)
from session_table_builders import (
    GAMMA_CWD,
    MODEL,
    OTHER_MODEL,
    PROVIDER,
    SENTINEL,
    Line,
    ts,
    write_jsonl,
    write_jsonl_zst,
)

from agentic_telemetry.session_table.codex import read_codex
from agentic_telemetry.session_table.row import SessionRow


def _write(tmp_path: Path, lines: list[Line], session_id: str = "codex-main-0001") -> Path:
    return write_jsonl(tmp_path / "2026" / "10" / "20" / codex_rollout_name(session_id), lines)


def _row(path: Path) -> SessionRow:
    read = read_codex(path)
    assert read.row is not None
    return read.row


def test_identity_comes_from_session_meta(tmp_path: Path) -> None:
    path = _write(tmp_path, [codex_meta("codex-main-0001"), codex_turn_context()])

    row = _row(path)

    assert row.client == "codex"
    assert row.session_id == "codex-main-0001"
    assert row.source_path == str(path)
    assert row.cwd == GAMMA_CWD
    assert row.project == "gamma-project"
    assert row.client_version == "0.50.0"
    assert row.provider == PROVIDER
    assert row.git_branch is None
    assert row.is_subagent is False
    assert row.parent_session_id is None
    assert row.stop_reasons is None, "Codex records no stop reasons"


def test_subagent_names_its_parent_thread(tmp_path: Path) -> None:
    meta = codex_meta(
        "codex-child-0001",
        source={"subagent": {"thread_spawn": {"depth": 1}}},
        parent_thread_id="codex-main-0001",
    )
    path = _write(tmp_path, [meta, codex_turn_context()], "codex-child-0001")

    row = _row(path)

    assert row.is_subagent is True
    assert row.parent_session_id == "codex-main-0001"


def test_usage_is_the_last_non_null_total_with_cached_input_subtracted(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            codex_meta(),
            codex_turn_context(),
            codex_token_count(input_tokens=300, cached=100, cache_write=10, output=40, reasoning=9),
            codex_token_count(
                input_tokens=1000, cached=600, cache_write=50, output=200, reasoning=80
            ),
            codex_token_count_without_info(),
        ],
    )

    row = _row(path)

    assert row.tokens_in == 400, "input_tokens includes cached_input_tokens"
    assert row.tokens_cache_read == 600
    assert row.tokens_cache_write == 50
    assert row.tokens_out == 200
    assert row.tokens_reasoning == 80


def test_assistant_turns_count_token_counts_with_info(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            codex_meta(),
            codex_turn_context(),
            codex_token_count_without_info(),
            codex_token_count(output=10),
            codex_token_count_without_info(),
            codex_token_count(output=20),
            codex_token_count(output=30),
        ],
    )

    assert _row(path).assistant_turns == 3


def test_token_count_repeating_the_previous_totals_is_not_a_new_turn(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            codex_meta(),
            codex_turn_context(),
            codex_token_count(output=10),
            codex_token_count(output=10),
            codex_token_count(output=20),
            codex_token_count(output=20),
        ],
    )

    row = _row(path)

    assert row.assistant_turns == 2
    assert row.tokens_out == 20


def test_turns_are_credited_to_the_current_turn_context_model(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            codex_meta(),
            codex_turn_context(OTHER_MODEL),
            codex_token_count(output=10),
            codex_turn_context(MODEL),
            codex_token_count(output=20),
            codex_token_count_without_info(),
            codex_token_count(output=30),
        ],
    )

    row = _row(path)

    assert row.models == (OTHER_MODEL, MODEL)
    assert row.primary_model == MODEL


def test_without_cached_input_tokens_uncached_input_is_null(tmp_path: Path) -> None:
    path = _write(
        tmp_path, [codex_meta(), codex_token_count(drop=("cached_input_tokens",), output=9)]
    )

    row = _row(path)

    assert row.tokens_in is None
    assert row.tokens_cache_read is None
    assert row.tokens_out == 9


def test_no_token_count_with_info_leaves_usage_null(tmp_path: Path) -> None:
    path = _write(tmp_path, [codex_meta(), codex_turn_context(), codex_token_count_without_info()])

    row = _row(path)

    assert row.tokens_in is None
    assert row.tokens_cache_read is None
    assert row.tokens_cache_write is None
    assert row.tokens_out is None
    assert row.tokens_reasoning is None
    assert row.assistant_turns is None, "turns are only known from token_count usage"


def test_activity_counts_follow_the_codex_rules(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            codex_meta(),
            codex_turn_context(),
            codex_user_response_item(),
            codex_item_completed("UserMessage"),
            codex_response_item("function_call", name="shell", arguments="{}"),
            codex_response_item("function_call_output", output=SENTINEL),
            codex_response_item("custom_tool_call", name="apply_patch", input=SENTINEL),
            codex_response_item("custom_tool_call_output", output=SENTINEL),
            codex_response_item("message", role="assistant", content=[]),
            codex_item_completed("CommandExecution", status="failed"),
            codex_item_completed("CommandExecution", status="completed"),
            codex_item_completed("McpToolCall", status="failed"),
            codex_item_completed("FileChange", status="failed"),
            codex_token_count(),
            codex_compacted(),
            codex_task_complete(),
            codex_user_response_item(),
            codex_item_completed("UserMessage"),
            codex_response_item("function_call", name="shell", arguments="{}"),
            codex_token_count(),
            codex_compacted(),
            codex_line("event_msg", {"type": "agent_message", "message": SENTINEL}),
        ],
    )

    row = _row(path)

    assert row.user_prompts == 2, "UserMessage items, not response_item user messages"
    assert row.tool_calls == 3, "function_call and custom_tool_call response items"
    assert row.tool_errors == 3, "failed items"
    assert row.compactions == 2, "top-level compacted lines"


def test_times_span_the_earliest_and_latest_line(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [codex_meta(timestamp=ts(0)), codex_turn_context(timestamp=ts(0)), codex_task_complete()],
    )

    row = _row(path)

    assert row.started_at is not None
    assert row.ended_at is not None
    assert datetime.fromisoformat(row.started_at) == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert datetime.fromisoformat(row.ended_at) == datetime(2026, 10, 20, 10, 2, tzinfo=UTC)
    assert row.span_seconds == 120.0


def test_compressed_rollout_reads_like_a_plain_one(tmp_path: Path) -> None:
    path = write_jsonl_zst(
        tmp_path / (codex_rollout_name("codex-zst-0001") + ".zst"),
        [codex_meta("codex-zst-0001"), codex_turn_context(), codex_token_count()],
    )

    read = read_codex(path)

    assert read.row is not None
    assert read.row.session_id == "codex-zst-0001"
    assert read.row.source_path == str(path)
    assert read.row.tokens_in == 400
    assert read.line_count == 3


def test_malformed_line_warns_and_mistyped_fields_become_nulls(tmp_path: Path) -> None:
    meta = codex_meta(cwd=["not", "a", "path"])
    path = _write(
        tmp_path,
        [
            meta,
            "{" + SENTINEL,
            codex_turn_context(model=17),
            codex_line("event_msg", {"type": "token_count", "info": "not-an-object"}),
            codex_line("event_msg", {"type": "item_completed", "item": None}),
            {"type": "response_item", "payload": "not-an-object"},
        ],
    )

    read = read_codex(path)

    assert read.row is not None
    assert read.row.cwd is None
    assert read.row.project is None
    assert read.row.models == ()
    assert read.row.tokens_in is None
    assert len(read.warnings) == 1
    assert read.warnings[0].startswith(f"{path}:2:")
    assert SENTINEL not in read.warnings[0]


def test_codex_records_no_served_model(tmp_path: Path) -> None:
    path = _write(tmp_path, [codex_meta(), codex_turn_context(), codex_token_count()])

    row = _row(path)

    assert row.models == (MODEL,)
    assert row.served_models == ()
    assert row.primary_served_model is None
