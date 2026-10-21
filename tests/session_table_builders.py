"""Synthetic Pi, Claude Code and Codex transcript lines for session-table tests.

Every value here is made up: session IDs, models, providers and the
`/workspace/...` cwds. `SENTINEL` stands in for transcript content, so tests can
check that stdout never echoes it. No line is copied from a real transcript.
This module imports nothing from `agentic_telemetry`, so `conftest.py` can use it
before the session-table package exists.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import zstandard

JsonObject = dict[str, object]
Line = Mapping[str, object] | str | bytes

SENTINEL = "TRANSCRIPT-CONTENT-SENTINEL"
ALPHA_CWD = "/workspace/alpha-project"
BETA_CWD = "/workspace/beta-project"
GAMMA_CWD = "/workspace/gamma-project"
MODEL = "example-coder"
OTHER_MODEL = "example-reasoner"
PROVIDER = "example-provider"


def ts(minute: int = 0, second: int = 0, millis: int = 0) -> str:
    """Return a `2026-10-20T10:MM:SS.mmmZ` timestamp."""
    return f"2026-10-20T10:{minute:02d}:{second:02d}.{millis:03d}Z"


def render_jsonl(lines: Iterable[Line]) -> bytes:
    """Return JSONL bytes: mappings as JSON, str items raw, bytes items as given."""
    out = bytearray()
    for line in lines:
        if isinstance(line, bytes):
            out += line
        elif isinstance(line, str):
            out += line.encode("utf-8")
        else:
            out += json.dumps(line).encode("utf-8")
        out += b"\n"
    return bytes(out)


def write_jsonl(path: Path, lines: Iterable[Line]) -> Path:
    """Write a plain `.jsonl` file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_jsonl(lines))
    return path


def write_jsonl_zst(path: Path, lines: Iterable[Line]) -> Path:
    """Write a zstandard-compressed `.jsonl.zst` file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zstandard.ZstdCompressor().compress(render_jsonl(lines)))
    return path


# --- Pi ---------------------------------------------------------------------


def pi_header(
    session_id: str = "pi-main-0001",
    *,
    cwd: object = ALPHA_CWD,
    timestamp: str = ts(),
    parent_session: str | None = None,
    drop: Sequence[str] = (),
) -> JsonObject:
    """Return a Pi session header line (the first line of a Pi transcript)."""
    header: JsonObject = {
        "type": "session",
        "version": 3,
        "id": session_id,
        "timestamp": timestamp,
        "cwd": cwd,
    }
    if parent_session is not None:
        header["parentSession"] = parent_session
    for key in drop:
        header.pop(key, None)
    return header


def pi_usage(
    *,
    tokens_in: object = 100,
    tokens_out: object = 20,
    cache_read: object = 0,
    cache_write: object = 0,
    reasoning: object | None = None,
) -> JsonObject:
    """Return a Pi assistant `usage` object; `reasoning` is omitted when None."""
    usage: JsonObject = {
        "input": tokens_in,
        "output": tokens_out,
        "cacheRead": cache_read,
        "cacheWrite": cache_write,
        "totalTokens": 0,
        "cost": {"input": 0.0, "output": 0.0, "total": 0.0},
    }
    if reasoning is not None:
        usage["reasoning"] = reasoning
    return usage


def pi_entry(entry_type: str, *, timestamp: str = ts(), **fields: object) -> JsonObject:
    """Return a Pi entry line of any type."""
    return {"type": entry_type, "id": "e-1", "parentId": "e-0", "timestamp": timestamp, **fields}


def pi_assistant(
    *,
    model: object = MODEL,
    provider: object = PROVIDER,
    stop_reason: object = "stop",
    usage: Mapping[str, object] | None = None,
    omit_usage: bool = False,
    tool_calls: int = 0,
    timestamp: str = ts(1),
) -> JsonObject:
    """Return a Pi assistant message with thinking, text and `tool_calls` toolCall items."""
    content: list[JsonObject] = [
        {"type": "thinking", "thinking": SENTINEL},
        {"type": "text", "text": SENTINEL},
    ]
    content += [
        {"type": "toolCall", "id": f"call-{n}", "name": "bash", "arguments": {"cmd": SENTINEL}}
        for n in range(tool_calls)
    ]
    message: JsonObject = {
        "role": "assistant",
        "content": content,
        "model": model,
        "provider": provider,
        "stopReason": stop_reason,
        "durationMs": 1200,
    }
    if not omit_usage:
        message["usage"] = dict(usage) if usage is not None else pi_usage()
    return pi_entry("message", timestamp=timestamp, message=message)


def pi_tool_result(*, is_error: object = False, timestamp: str = ts(1, 30)) -> JsonObject:
    """Return a Pi toolResult message."""
    message: JsonObject = {
        "role": "toolResult",
        "toolCallId": "call-0",
        "toolName": "bash",
        "content": [{"type": "text", "text": SENTINEL}],
        "isError": is_error,
    }
    return pi_entry("message", timestamp=timestamp, message=message)


def pi_user(content: object = None, *, timestamp: str = ts()) -> JsonObject:
    """Return a Pi user message; the default content is one text item."""
    if content is None:
        content = [{"type": "text", "text": SENTINEL}]
    return pi_entry("message", timestamp=timestamp, message={"role": "user", "content": content})


def pi_compaction(*, timestamp: str = ts(3)) -> JsonObject:
    """Return a Pi compaction entry."""
    return pi_entry("compaction", timestamp=timestamp, summary=SENTINEL, tokensBefore=50000)


def pi_session_file_name(session_id: str) -> str:
    """Return Pi's `<timestamp>_<session-id>.jsonl` file name."""
    return f"2026-10-20T10-00-00-000Z_{session_id}.jsonl"
