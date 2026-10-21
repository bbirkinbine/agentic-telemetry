"""Summarize one Codex CLI rollout (`rollout-<ts>-<id>.jsonl`, or `.jsonl.zst`).

Every line is `{timestamp, type, payload}`. `session_meta` names the session;
`turn_context` sets the model for the turns that follow. Each `token_count`
event with usage `info` is one model response, and its `total_token_usage` is
cumulative, so the session's usage is the last one recorded; an event that
repeats the previous total is not a new response. Codex counts
cached input inside `input_tokens`; `tokens_in` subtracts it so the column is
uncached input for every client.
"""

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from agentic_telemetry.run_table.json_values import JsonObject, as_object, opt_int, opt_str
from agentic_telemetry.session_table.discovery import delegate_run_id
from agentic_telemetry.session_table.jsonl_lines import JsonlLines
from agentic_telemetry.session_table.row import SessionIdentity, SessionRead
from agentic_telemetry.session_table.tally import SessionTally

TOOL_CALL_ITEMS = frozenset({"function_call", "custom_tool_call"})


@dataclass
class _Rollout:
    """State carried across lines: the session metadata, current model and latest totals."""

    meta: JsonObject | None = None
    model: object = None
    totals: JsonObject | None = None


def read_codex(path: Path) -> SessionRead:
    """Read a Codex rollout into one session row.

    Args:
        path: The rollout file, plain or zstandard-compressed.

    Returns:
        The row (None when `session_meta` has no ID), the physical line count,
        and one warning per skipped line.

    Raises:
        OSError: The file cannot be read.
        zstandard.ZstdError: A `.zst` file is not valid zstandard data.
    """
    lines = JsonlLines(path)
    tally = SessionTally()
    rollout = _Rollout()
    for _, record in lines:
        tally.observe_timestamp(record.get("timestamp"))
        _read_line(record, rollout, tally)
    meta = rollout.meta or {}
    session_id = opt_str(meta.get("id"))
    if not session_id:
        return SessionRead(None, lines.line_count, lines.warnings)
    if rollout.totals is not None:
        _add_totals(rollout.totals, tally)
    source = as_object(meta.get("source"))
    identity = SessionIdentity(
        client="codex",
        session_id=session_id,
        source_path=str(path),
        cwd=opt_str(meta.get("cwd")),
        client_version=opt_str(meta.get("cli_version")),
        is_subagent=source is not None and "subagent" in source,
        parent_session_id=opt_str(meta.get("parent_thread_id")),
        run_id=delegate_run_id(path),
        provider=opt_str(meta.get("model_provider")),
    )
    row = tally.to_row(identity)
    if tally.assistant_turns == 0:
        # Turns are only known from usage records; with none, the count is unknown, not 0.
        row = dataclasses.replace(row, assistant_turns=None)
    return SessionRead(row, lines.line_count, lines.warnings)


def _read_line(record: JsonObject, rollout: _Rollout, tally: SessionTally) -> None:
    """Fold one rollout line into the rollout state and the tally."""
    line_type = record.get("type")
    if line_type == "compacted":
        tally.compactions += 1
        return
    payload = as_object(record.get("payload"))
    if payload is None:
        return
    payload_type = payload.get("type")
    if line_type == "session_meta" and rollout.meta is None:
        rollout.meta = payload
    elif line_type == "turn_context":
        rollout.model = payload.get("model")
    elif line_type == "response_item" and payload_type in TOOL_CALL_ITEMS:
        tally.tool_calls += 1
    elif line_type == "event_msg" and payload_type == "token_count":
        _read_token_count(payload, rollout, tally)
    elif line_type == "event_msg" and payload_type == "item_completed":
        _read_completed_item(payload, tally)


def _read_token_count(payload: JsonObject, rollout: _Rollout, tally: SessionTally) -> None:
    """Count one model response and keep its cumulative totals.

    Codex re-sends the current totals in some `token_count` events (observed:
    236 of 8,673 on the owner's machine). An event whose totals equal the
    previous ones is not counted as a new turn.
    """
    info = as_object(payload.get("info"))
    if info is None:
        return
    totals = as_object(info.get("total_token_usage"))
    if totals is not None and rollout.totals is not None and totals == rollout.totals:
        return
    tally.count_assistant_turn(rollout.model)
    if totals is not None:
        rollout.totals = totals


def _read_completed_item(payload: JsonObject, tally: SessionTally) -> None:
    """Count a failed tool item as a tool error and a `UserMessage` item as a prompt."""
    item = as_object(payload.get("item"))
    if item is None:
        return
    if item.get("status") == "failed":
        tally.tool_errors += 1
    if item.get("type") == "UserMessage":
        tally.user_prompts += 1


def _add_totals(totals: JsonObject, tally: SessionTally) -> None:
    """Record the session's cumulative usage, with cached input moved out of `tokens_in`."""
    input_tokens = opt_int(totals.get("input_tokens"))
    cached = opt_int(totals.get("cached_input_tokens"))
    uncached = input_tokens - cached if input_tokens is not None and cached is not None else None
    tally.add_usage(
        tokens_in=uncached,
        tokens_cache_read=cached,
        tokens_cache_write=totals.get("cache_write_input_tokens"),
        tokens_out=totals.get("output_tokens"),
        tokens_reasoning=totals.get("reasoning_output_tokens"),
    )
