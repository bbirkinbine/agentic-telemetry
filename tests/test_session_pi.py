"""Pi transcripts: one session row per file, summed usage, activity counts, lineage."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from session_table_builders import (
    ALPHA_CWD,
    MODEL,
    OTHER_MODEL,
    SENTINEL,
    Line,
    pi_assistant,
    pi_compaction,
    pi_entry,
    pi_header,
    pi_session_file_name,
    pi_tool_result,
    pi_usage,
    pi_user,
    ts,
    write_jsonl,
)

from agentic_telemetry.session_table.pi import read_pi
from agentic_telemetry.session_table.row import SessionRow


def _row(path: Path) -> SessionRow:
    read = read_pi(path)
    assert read.row is not None
    return read.row


def _write(tmp_path: Path, lines: list[Line], session_id: str = "pi-main-0001") -> Path:
    return write_jsonl(tmp_path / pi_session_file_name(session_id), lines)


def test_identity_comes_from_the_header(tmp_path: Path) -> None:
    path = _write(tmp_path, [pi_header("pi-main-0001"), pi_assistant()])

    row = _row(path)

    assert row.client == "pi"
    assert row.session_id == "pi-main-0001"
    assert row.source_path == str(path)
    assert row.cwd == ALPHA_CWD
    assert row.project == "alpha-project"
    assert row.client_version is None, "the header version is the format version, not the CLI's"
    assert row.git_branch is None
    assert row.is_subagent is False
    assert row.parent_session_id is None
    assert row.run_id is None


PARENT_PATH = "/workspace/sessions/--workspace-alpha-project--/" + pi_session_file_name(
    "pi-parent-0001"
)


def test_subagent_child_under_its_parents_directory_names_the_parent(tmp_path: Path) -> None:
    parent_stem = pi_session_file_name("pi-parent-0001").removesuffix(".jsonl")
    path = write_jsonl(
        tmp_path / parent_stem / "agent-0001" / "run-0" / pi_session_file_name("pi-child-0001"),
        [pi_header("pi-child-0001", parent_session=PARENT_PATH), pi_assistant()],
    )

    row = _row(path)

    assert row.is_subagent is True
    assert row.parent_session_id == "pi-parent-0001"


def test_fork_beside_its_parent_has_a_parent_but_is_not_a_subagent(tmp_path: Path) -> None:
    path = _write(
        tmp_path, [pi_header("pi-fork-0001", parent_session=PARENT_PATH), pi_assistant()], "f"
    )

    row = _row(path)

    assert row.is_subagent is False
    assert row.parent_session_id == "pi-parent-0001"


def test_usage_is_summed_over_assistant_messages(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            pi_header(),
            pi_assistant(usage=pi_usage(tokens_in=100, tokens_out=20, cache_read=300)),
            pi_assistant(
                usage=pi_usage(
                    tokens_in=50, tokens_out=5, cache_read=0, cache_write=40, reasoning=7
                )
            ),
        ],
    )

    row = _row(path)

    assert row.tokens_in == 150
    assert row.tokens_out == 25
    assert row.tokens_cache_read == 300
    assert row.tokens_cache_write == 40
    assert row.tokens_reasoning == 7
    assert row.assistant_turns == 2


def test_no_usage_recorded_leaves_token_columns_null(tmp_path: Path) -> None:
    path = _write(tmp_path, [pi_header(), pi_assistant(omit_usage=True)])

    row = _row(path)

    assert row.tokens_in is None
    assert row.tokens_out is None
    assert row.tokens_cache_read is None
    assert row.tokens_reasoning is None


def test_models_primary_model_and_its_first_turns_provider(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            pi_header(),
            pi_assistant(model=MODEL, provider="provider-one"),
            pi_assistant(model=OTHER_MODEL, provider="provider-two"),
            pi_assistant(model=OTHER_MODEL, provider="provider-three"),
        ],
    )

    row = _row(path)

    assert row.models == (MODEL, OTHER_MODEL)
    assert row.primary_model == OTHER_MODEL
    assert row.provider == "provider-two"


def test_activity_counts_follow_the_pi_rules(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            pi_header(),
            pi_user(SENTINEL),
            pi_user(""),
            pi_user([{"type": "image", "data": SENTINEL, "mimeType": "image/png"}]),
            pi_assistant(stop_reason="toolUse", tool_calls=2),
            pi_tool_result(is_error=False),
            pi_tool_result(is_error=True),
            pi_assistant(stop_reason="toolUse", tool_calls=1),
            pi_tool_result(is_error=False),
            pi_compaction(),
            pi_user(),
            pi_assistant(stop_reason="stop"),
            pi_compaction(),
            pi_entry("model_change", provider="p", modelId=OTHER_MODEL),
            pi_entry("custom_message", content=SENTINEL),
        ],
    )

    row = _row(path)

    assert row.user_prompts == 2, "non-empty string content, or a list holding a text item"
    assert row.tool_calls == 3, "toolCall content items"
    assert row.tool_errors == 1, "toolResult with isError true"
    assert row.compactions == 2
    assert row.assistant_turns == 3
    assert row.stop_reasons is not None
    assert json.loads(row.stop_reasons) == {"toolUse": 2, "stop": 1}


def test_start_end_and_span_come_from_entry_timestamps(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            pi_header(timestamp=ts(0)),
            pi_user(timestamp=ts(0)),
            pi_assistant(timestamp=ts(1)),
            pi_compaction(timestamp=ts(4, 30)),
        ],
    )

    row = _row(path)

    assert row.started_at is not None
    assert row.ended_at is not None
    assert datetime.fromisoformat(row.started_at) == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert datetime.fromisoformat(row.ended_at) == datetime(2026, 10, 20, 10, 4, 30, tzinfo=UTC)
    assert row.span_seconds == pytest.approx(270.0)


def test_malformed_line_warns_with_location_and_the_rest_still_counts(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [pi_header(), pi_assistant(), '{"type": "message", "x": "' + SENTINEL, pi_assistant()],
    )

    read = read_pi(path)

    assert read.row is not None
    assert read.row.assistant_turns == 2
    assert read.line_count == 4
    assert len(read.warnings) == 1
    assert read.warnings[0].startswith(f"{path}:3:")
    assert SENTINEL not in read.warnings[0]


def test_mistyped_fields_become_nulls_without_raising(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            pi_header(cwd=42),
            pi_assistant(model=7, provider=["p"], usage=pi_usage(tokens_in="12", tokens_out=3)),
            pi_entry("message", message="not-an-object"),
            pi_entry("message", message={"role": "assistant", "content": "not-a-list"}),
            pi_tool_result(is_error="yes"),
        ],
    )

    row = _row(path)

    assert row.cwd is None
    assert row.project is None
    assert row.models == ()
    assert row.primary_model is None
    assert row.tokens_in is None
    assert row.tokens_out == 3
    assert row.tool_errors == 0


def test_header_without_an_id_gives_no_row(tmp_path: Path) -> None:
    path = _write(tmp_path, [pi_header(drop=("id",)), pi_assistant()])

    assert read_pi(path).row is None


def test_parent_with_an_empty_file_stem_is_not_a_subagent(tmp_path: Path) -> None:
    path = _write(tmp_path, [pi_header("pi-odd-0001", parent_session="/"), pi_assistant()], "o")

    assert _row(path).is_subagent is False


def test_served_model_is_the_response_model_when_recorded_else_the_model(tmp_path: Path) -> None:
    served = pi_assistant(model="local-alias")
    served_message = served["message"]
    assert isinstance(served_message, dict)
    served_message["responseModel"] = "weights-q8"
    path = _write(tmp_path, [pi_header(), served, served, pi_assistant(model=MODEL)])

    row = _row(path)

    assert row.models == ("local-alias", MODEL)
    assert row.served_models == ("weights-q8", MODEL)
    assert row.primary_served_model == "weights-q8"
