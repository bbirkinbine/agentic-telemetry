"""Synthetic Codex CLI rollout lines for session-table tests.

Every value is made up; `SENTINEL` stands in for content. No line is copied
from a real rollout.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from session_table_builders import GAMMA_CWD, MODEL, PROVIDER, SENTINEL, JsonObject, ts


def codex_line(
    line_type: str, payload: Mapping[str, object], *, timestamp: str = ts()
) -> JsonObject:
    """Return one Codex rollout line."""
    return {"timestamp": timestamp, "type": line_type, "payload": dict(payload)}


def codex_meta(
    session_id: str = "codex-main-0001",
    *,
    cwd: object = GAMMA_CWD,
    source: object = "cli",
    parent_thread_id: str | None = None,
    timestamp: str = ts(),
) -> JsonObject:
    """Return a Codex `session_meta` line; pass a subagent `source` for a child thread."""
    payload: JsonObject = {
        "id": session_id,
        "timestamp": timestamp,
        "cwd": cwd,
        "originator": "codex_cli_rs",
        "cli_version": "0.50.0",
        "source": source,
        "model_provider": PROVIDER,
    }
    if parent_thread_id is not None:
        payload["parent_thread_id"] = parent_thread_id
    return codex_line("session_meta", payload, timestamp=timestamp)


def codex_turn_context(model: object = MODEL, *, timestamp: str = ts()) -> JsonObject:
    """Return a Codex `turn_context` line naming the model."""
    payload = {"cwd": GAMMA_CWD, "model": model, "effort": "medium"}
    return codex_line("turn_context", payload, timestamp=timestamp)


def codex_token_count(
    *,
    input_tokens: object = 1000,
    cached: object = 600,
    cache_write: object = 50,
    output: object = 200,
    reasoning: object = 80,
    drop: Sequence[str] = (),
    timestamp: str = ts(1),
) -> JsonObject:
    """Return a Codex token_count event with cumulative `total_token_usage`."""
    totals: JsonObject = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": cache_write,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "total_tokens": 0,
    }
    for key in drop:
        totals.pop(key, None)
    info = {"total_token_usage": totals, "last_token_usage": totals, "model_context_window": 1}
    return codex_line("event_msg", {"type": "token_count", "info": info}, timestamp=timestamp)


def codex_token_count_without_info(*, timestamp: str = ts(1)) -> JsonObject:
    """Return a Codex token_count event whose `info` is null (rate limits only)."""
    payload = {"type": "token_count", "info": None, "rate_limits": {}}
    return codex_line("event_msg", payload, timestamp=timestamp)


def codex_response_item(item_type: str, *, timestamp: str = ts(), **fields: object) -> JsonObject:
    """Return a Codex response_item line, e.g. `function_call` or `custom_tool_call`."""
    return codex_line("response_item", {"type": item_type, **fields}, timestamp=timestamp)


def codex_user_response_item(*, timestamp: str = ts()) -> JsonObject:
    """Return a response_item user message (carries injected context, not a prompt)."""
    content = [{"type": "input_text", "text": SENTINEL}]
    return codex_response_item("message", role="user", content=content, timestamp=timestamp)


def codex_item_completed(
    item_type: str, *, status: str = "completed", timestamp: str = ts()
) -> JsonObject:
    """Return an `item_completed` event for one item."""
    payload = {"type": "item_completed", "item": {"type": item_type, "status": status}}
    return codex_line("event_msg", payload, timestamp=timestamp)


def codex_compacted(*, timestamp: str = ts(2)) -> JsonObject:
    """Return a top-level Codex `compacted` line."""
    return codex_line("compacted", {"message": SENTINEL}, timestamp=timestamp)


def codex_task_complete(*, timestamp: str = ts(2)) -> JsonObject:
    """Return a Codex `task_complete` event."""
    return codex_line("event_msg", {"type": "task_complete"}, timestamp=timestamp)


def codex_rollout_name(session_id: str) -> str:
    """Return Codex's `rollout-<ts>-<id>.jsonl` file name."""
    return f"rollout-2026-10-20T10-00-00-{session_id}.jsonl"
