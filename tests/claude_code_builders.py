"""Synthetic Claude Code transcript lines for session-table tests.

Every value is made up; `SENTINEL` stands in for content. No line is copied
from a real transcript.
"""

from __future__ import annotations

from collections.abc import Mapping

from session_table_builders import BETA_CWD, MODEL, SENTINEL, JsonObject, ts


def cc_line(
    line_type: str,
    *,
    session_id: str = "cc-main-0001",
    timestamp: str = ts(),
    cwd: object = BETA_CWD,
    version: object = "2.0.1",
    git_branch: object = "main",
    agent_id: str | None = None,
    **fields: object,
) -> JsonObject:
    """Return a Claude Code transcript line; `agent_id` makes it a subagent line."""
    line: JsonObject = {
        "type": line_type,
        "parentUuid": None,
        "isSidechain": agent_id is not None,
        "userType": "external",
        "cwd": cwd,
        "sessionId": session_id,
        "version": version,
        "gitBranch": git_branch,
        "uuid": "u-1",
        "timestamp": timestamp,
    }
    if agent_id is not None:
        line["agentId"] = agent_id
    line.update(fields)
    return line


def cc_text_block() -> JsonObject:
    """Return a Claude Code text content block."""
    return {"type": "text", "text": SENTINEL}


def cc_tool_use_block() -> JsonObject:
    """Return a Claude Code tool_use content block."""
    return {"type": "tool_use", "id": "toolu-1", "name": "Bash", "input": {"command": SENTINEL}}


def cc_tool_result_block(*, is_error: object = False) -> JsonObject:
    """Return a Claude Code tool_result content block."""
    return {
        "type": "tool_result",
        "tool_use_id": "toolu-1",
        "content": SENTINEL,
        "is_error": is_error,
    }


def cc_user(content: object = SENTINEL, **kwargs: object) -> JsonObject:
    """Return a Claude Code user line; extra keywords such as `isMeta` go on the line."""
    return cc_line("user", message={"role": "user", "content": content}, **kwargs)


def cc_usage(
    *,
    tokens_in: object = 100,
    cache_write: object = 0,
    cache_read: object = 0,
    tokens_out: object = 20,
) -> JsonObject:
    """Return a Claude Code `message.usage` object."""
    return {
        "input_tokens": tokens_in,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
        "output_tokens": tokens_out,
        "service_tier": "standard",
    }


def cc_assistant(
    *,
    message_id: str | None = "msg-0001",
    block: Mapping[str, object] | None = None,
    model: object = MODEL,
    stop_reason: object = None,
    usage: Mapping[str, object] | None = None,
    **kwargs: object,
) -> JsonObject:
    """Return one Claude Code assistant line holding one content block."""
    message: JsonObject = {
        "model": model,
        "role": "assistant",
        "type": "message",
        "content": [dict(block) if block is not None else cc_text_block()],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": dict(usage) if usage is not None else cc_usage(),
    }
    if message_id is not None:
        message["id"] = message_id
    return cc_line("assistant", message=message, requestId="req-1", **kwargs)


def cc_system(subtype: str, **kwargs: object) -> JsonObject:
    """Return a Claude Code system line."""
    return cc_line("system", subtype=subtype, content=SENTINEL, level="info", **kwargs)


def cc_snapshot() -> JsonObject:
    """Return a Claude Code file-history-snapshot line, which has no sessionId."""
    return {"type": "file-history-snapshot", "messageId": "u-1", "snapshot": {"files": {}}}
