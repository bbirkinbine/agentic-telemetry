"""A subagent's model names go to `subagent_models`, never `requested_models` (spec 0009).

`requested_models` stays the parent's own names, which the server-metrics join
resolves; a child's name there could make it resolve the child's model instead.
"""

from __future__ import annotations

from pathlib import Path

from run_table_builders import assistant_message, session_header, write_session
from subagent_builders import CHILD_MODEL, OTHER_PROVIDER, PROVIDER, subagent_tool_result

from agentic_telemetry.run_table.session_usage import SessionUsage, read_session_usage


def usage_of(tmp_path: Path, *lines: dict[str, object]) -> SessionUsage:
    """Write one transcript with a header and `lines`, and return its usage."""
    run_dir = tmp_path / "results" / "run-handoff"
    write_session(run_dir, [session_header(), *lines])
    usage = read_session_usage(run_dir / "session")
    assert usage is not None
    return usage


def test_subagent_model_loses_the_provider_prefix_of_a_parent_message(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        assistant_message(tokens_in=100, tokens_out=20, provider=PROVIDER),
        subagent_tool_result(tokens_in=10, tokens_out=1, models=(f"{PROVIDER}/{CHILD_MODEL}",)),
    )

    assert usage.subagent_models == (CHILD_MODEL,)


def test_only_the_prefix_up_to_the_first_slash_is_removed(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        assistant_message(tokens_in=100, tokens_out=20, provider=PROVIDER),
        subagent_tool_result(
            tokens_in=10, tokens_out=1, models=(f"{PROVIDER}/vendor-example/local-coder",)
        ),
    )

    assert usage.subagent_models == ("vendor-example/local-coder",)


def test_subagent_model_with_an_unknown_provider_is_kept_whole(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        assistant_message(tokens_in=100, tokens_out=20, provider=PROVIDER),
        subagent_tool_result(
            tokens_in=10, tokens_out=1, models=(f"{OTHER_PROVIDER}/local-other-coder",)
        ),
    )

    assert usage.subagent_models == (f"{OTHER_PROVIDER}/local-other-coder",)


def test_subagent_model_without_a_slash_is_kept_whole(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        assistant_message(tokens_in=100, tokens_out=20, provider=PROVIDER),
        subagent_tool_result(tokens_in=10, tokens_out=1, models=("local-plain-coder",)),
    )

    assert usage.subagent_models == ("local-plain-coder",)


def test_child_names_never_join_the_parents_requested_names(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        assistant_message(
            tokens_in=100,
            tokens_out=20,
            model=CHILD_MODEL,
            response_model="served-flash-next-q8",
            provider=PROVIDER,
            message_id="m-1",
        ),
        subagent_tool_result(
            tokens_in=10,
            tokens_out=1,
            models=(f"{PROVIDER}/{CHILD_MODEL}", f"{PROVIDER}/local-helper-coder"),
        ),
        assistant_message(
            tokens_in=100, tokens_out=20, model="local-review-model", provider=PROVIDER
        ),
    )

    assert usage.requested_models == (CHILD_MODEL, "local-review-model")
    assert usage.served_models == ("served-flash-next-q8",)
    assert usage.subagent_models == (CHILD_MODEL, "local-helper-coder")


def test_names_that_strip_to_the_same_model_appear_once(tmp_path: Path) -> None:
    usage = usage_of(
        tmp_path,
        assistant_message(tokens_in=100, tokens_out=20, provider=PROVIDER),
        subagent_tool_result(
            tokens_in=10,
            tokens_out=1,
            models=(f"{PROVIDER}/{CHILD_MODEL}", CHILD_MODEL),
            message_id="m-a",
        ),
        subagent_tool_result(
            tokens_in=10, tokens_out=1, models=(f"{PROVIDER}/{CHILD_MODEL}",), message_id="m-b"
        ),
    )

    assert usage.subagent_models == (CHILD_MODEL,)


def test_provider_match_works_across_transcript_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "run-handoff"
    write_session(
        run_dir,
        [session_header("s-one"), subagent_tool_result(tokens_in=10, tokens_out=1)],
        name="0001.jsonl",
    )
    write_session(
        run_dir,
        [session_header("s-two"), assistant_message(tokens_in=1, tokens_out=1, provider=PROVIDER)],
        name="0002.jsonl",
    )

    usage = read_session_usage(run_dir / "session")

    assert usage is not None
    assert usage.subagent_models == (CHILD_MODEL,)
    assert usage.requested_models == ()
