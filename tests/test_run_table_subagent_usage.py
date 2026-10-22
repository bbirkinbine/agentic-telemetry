"""Session usage counts a Pi `subagent` tool result's usage, once (spec 0009)."""

from __future__ import annotations

from pathlib import Path

from run_table_builders import assistant_message, session_header, write_session
from subagent_builders import (
    management_tool_result,
    subagent_tool_result,
    write_nested_subagent_copies,
)

from agentic_telemetry.run_table.session_usage import SessionUsage, read_session_usage

PARENT_SESSION = "2026-10-20T10-00-00-000Z_parent-0001"


def usage_of(tmp_path: Path, *lines: dict[str, object]) -> SessionUsage:
    """Write one transcript with a header and `lines`, and return its usage."""
    run_dir = tmp_path / "results" / "run-handoff"
    write_session(run_dir, [session_header(), *lines])
    usage = read_session_usage(run_dir / "session")
    assert usage is not None
    return usage


def test_subagent_usage_is_added_to_the_summed_token_columns(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        assistant_message(tokens_in=100, tokens_out=20, cache_read=5, reasoning=3),
        subagent_tool_result(tokens_in=1000, tokens_out=300, cache_read=40, cache_write=7000),
        assistant_message(tokens_in=200, tokens_out=30, cache_read=7, reasoning=4),
    )

    assert usage.tokens_in == 1300
    assert usage.tokens_out == 350
    assert usage.tokens_cache_read == 52
    # Pi's tool result reports no reasoning, so this stays the parent's.
    assert usage.tokens_reasoning == 7
    assert usage.assistant_turns == 2


def test_a_reasoning_key_on_the_tool_result_would_still_be_summed(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        assistant_message(tokens_in=100, tokens_out=20, reasoning=3),
        subagent_tool_result(tokens_in=10, tokens_out=1, reasoning=11),
    )

    assert usage.tokens_reasoning == 14


def test_nested_subagent_copies_are_never_read_so_each_turn_counts_once(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "run-handoff"
    write_session(
        run_dir,
        [
            session_header("parent-0001"),
            assistant_message(tokens_in=30000, tokens_out=4000, message_id="m-1"),
            subagent_tool_result(tokens_in=20705, tokens_out=7291, turns=9, message_id="m-2"),
            assistant_message(tokens_in=30922, tokens_out=5598, message_id="m-3"),
        ],
        name=f"{PARENT_SESSION}.jsonl",
    )
    child_turns = [
        assistant_message(tokens_in=2300, tokens_out=810, message_id=f"c-{n}") for n in range(8)
    ]
    child_turns.append(assistant_message(tokens_in=2305, tokens_out=811, message_id="c-8"))
    write_nested_subagent_copies(
        run_dir, PARENT_SESSION, "sub-0001", "test-first", [session_header("child"), *child_turns]
    )

    usage = read_session_usage(run_dir / "session")

    assert usage is not None
    assert (usage.tokens_in, usage.tokens_out) == (81627, 16889)
    assert usage.assistant_turns == 2


def test_management_call_without_usage_adds_nothing(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        assistant_message(tokens_in=100, tokens_out=20, model="local-parent-model"),
        management_tool_result(),
    )

    assert (usage.tokens_in, usage.tokens_out) == (100, 20)
    assert usage.subagent_runs == 0
    assert usage.requested_models == ("local-parent-model",)
    assert usage.subagent_models == ()


def test_subagent_result_marked_as_error_still_counts(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        assistant_message(tokens_in=100, tokens_out=20),
        subagent_tool_result(tokens_in=500, tokens_out=60, is_error=True),
    )

    assert (usage.tokens_in, usage.tokens_out) == (600, 80)
    assert usage.subagent_runs == 1


def test_non_numeric_subagent_usage_values_count_as_zero(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        assistant_message(tokens_in=100, tokens_out=20),
        subagent_tool_result(
            tokens_in=0,
            tokens_out=0,
            usage={"input": "500", "output": None, "cacheRead": 7, "reasoning": True},
        ),
    )

    assert (usage.tokens_in, usage.tokens_out) == (100, 20)
    assert usage.tokens_cache_read == 7
    assert usage.tokens_reasoning == 0


def test_tool_result_from_another_tool_with_usage_is_ignored(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        assistant_message(tokens_in=100, tokens_out=20),
        subagent_tool_result(tokens_in=900, tokens_out=90, tool_name="bash"),
    )

    assert (usage.tokens_in, usage.tokens_out) == (100, 20)
    assert usage.subagent_models == ()


def test_subagent_runs_counts_every_result_entry_across_calls(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        subagent_tool_result(tokens_in=10, tokens_out=1, results=3, mode="parallel"),
        subagent_tool_result(tokens_in=10, tokens_out=1, results=1, message_id="m-sub-2"),
    )

    assert usage.subagent_runs == 4


def test_subagent_turns_sums_total_child_turns_across_calls(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        subagent_tool_result(tokens_in=10, tokens_out=1, turns=9),
        subagent_tool_result(tokens_in=10, tokens_out=1, turns=4, message_id="m-sub-2"),
    )

    assert usage.subagent_turns == 13


def test_subagent_turns_is_null_when_a_call_lacks_numeric_turns(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        subagent_tool_result(tokens_in=10, tokens_out=1, turns=9),
        subagent_tool_result(tokens_in=10, tokens_out=1, turns=None, message_id="m-sub-2"),
    )

    assert usage.subagent_runs == 2
    assert usage.subagent_turns is None


def test_management_call_without_turns_does_not_null_subagent_turns(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        subagent_tool_result(tokens_in=10, tokens_out=1, turns=9),
        management_tool_result(),
    )

    assert usage.subagent_runs == 1
    assert usage.subagent_turns == 9


def test_transcript_without_a_subagent_call_has_zero_runs_and_turns(tmp_path: Path) -> None:
    usage = usage_of(tmp_path, assistant_message(tokens_in=100, tokens_out=20))

    assert usage.subagent_runs == 0
    assert usage.subagent_turns == 0


def test_peak_turn_context_stays_the_parents_largest_turn(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        assistant_message(tokens_in=500, tokens_out=20, cache_read=300),
        subagent_tool_result(tokens_in=20000, tokens_out=900, cache_read=5000, cache_write=100),
        assistant_message(tokens_in=400, tokens_out=20),
    )

    assert usage.peak_turn_prompt_tokens == 800


def test_malformed_details_still_count_the_usage(tmp_path: Path) -> None:
    odd_entries = subagent_tool_result(tokens_in=10, tokens_out=2)
    odd_message = odd_entries["message"]
    assert isinstance(odd_message, dict)
    odd_message["details"] = {
        "results": [{"model": 7}, "not-an-object", {"model": "solo-coder"}],
        "totalChildUsage": "not-an-object",
    }
    no_details = subagent_tool_result(tokens_in=20, tokens_out=3, message_id="m-bare")
    bare_message = no_details["message"]
    assert isinstance(bare_message, dict)
    bare_message["details"] = "not-an-object"

    usage = usage_of(tmp_path, odd_entries, no_details)

    assert (usage.tokens_in, usage.tokens_out) == (30, 5)
    assert usage.subagent_runs == 2
    assert usage.subagent_turns is None
    assert usage.subagent_models == ("solo-coder",)
