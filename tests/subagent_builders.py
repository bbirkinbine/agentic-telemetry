"""Synthetic Pi subagent evidence for run-table tests (spec 0009).

The shapes follow the spec's description of a parent transcript's `subagent`
tool result: a `toolResult` message with `toolName: subagent`, a `usage` object
keyed like an assistant message's, and `details` with `results`,
`totalChildUsage`, `runId` and `artifacts`. Every value is made up: the provider
is `gpu-example`, the model names are invented, and no real transcript is copied.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

JsonObject = dict[str, object]

PROVIDER = "gpu-example"
OTHER_PROVIDER = "other-example"
CHILD_MODEL = "local-flash-next"
DEFAULT_CHILD_MODELS = (f"{PROVIDER}/{CHILD_MODEL}",)


def subagent_tool_result(
    *,
    tokens_in: int,
    tokens_out: int,
    cache_read: int = 0,
    cache_write: int = 0,
    reasoning: int | None = None,
    models: Sequence[str] = DEFAULT_CHILD_MODELS,
    results: int | None = None,
    turns: object = 9,
    is_error: bool = False,
    mode: str = "single",
    tool_name: str = "subagent",
    usage: Mapping[str, object] | None = None,
    message_id: str = "m-subagent",
) -> JsonObject:
    """Return one Pi transcript line for a subagent tool result carrying `usage`.

    Args:
        tokens_in: The usage's `input`.
        tokens_out: The usage's `output`.
        cache_read: The usage's `cacheRead`.
        cache_write: The usage's `cacheWrite` (never summed into a column).
        reasoning: A `reasoning` key in the usage; None (the default) leaves it out,
            as Pi does: its tool result reports no reasoning.
        models: `details.results[i].model` for the first `len(models)` entries.
        results: The number of `details.results` entries; defaults to
            `len(models)`, or 1 when `models` is empty. Entries past `models` have
            no `model` key.
        turns: `details.totalChildUsage.turns`; None leaves the key out.
        is_error: The message's `isError`.
        mode: `details.mode` (`single`, `parallel` or `chain`).
        tool_name: The message's `toolName`; another name models a different tool.
        usage: Replaces the whole `usage` object (for non-numeric values).
        message_id: The transcript line's `id`.
    """
    count = results if results is not None else max(len(models), 1)
    entries: list[JsonObject] = []
    for index in range(count):
        entry: JsonObject = {"agent": "test-first", "exitCode": 0, "index": index}
        if index < len(models):
            entry["model"] = models[index]
        entries.append(entry)
    child_usage: JsonObject = {"input": tokens_in, "output": tokens_out}
    if turns is not None:
        child_usage["turns"] = turns
    if usage is None:
        usage = {
            "input": tokens_in,
            "output": tokens_out,
            "cacheRead": cache_read,
            "cacheWrite": cache_write,
            "totalTokens": tokens_in + tokens_out + cache_read + cache_write,
            "cost": {"input": 0, "output": 0, "total": 0},
        }
        if reasoning is not None:
            usage = {**usage, "reasoning": reasoning}
    message: JsonObject = {
        "role": "toolResult",
        "toolCallId": f"call-{message_id}",
        "toolName": tool_name,
        "content": [{"type": "text", "text": "synthetic subagent output"}],
        "isError": is_error,
        "usage": dict(usage),
        "details": {
            "mode": mode,
            "runId": "sub-run-0001",
            "results": entries,
            "totalChildUsage": child_usage,
            "artifacts": {"dir": "subagent-artifacts"},
        },
    }
    return _message_line(message, message_id)


def management_tool_result(message_id: str = "m-subagent-management") -> JsonObject:
    """Return a subagent tool result for a management action: no `usage`, no `turns`."""
    message: JsonObject = {
        "role": "toolResult",
        "toolCallId": f"call-{message_id}",
        "toolName": "subagent",
        "content": [{"type": "text", "text": "synthetic agent list"}],
        "isError": False,
        "details": {"mode": "management", "results": []},
    }
    return _message_line(message, message_id)


def write_nested_subagent_copies(
    run_dir: Path,
    parent_session: str,
    call_id: str,
    agent: str,
    lines: Iterable[Mapping[str, object]],
) -> list[Path]:
    """Write the subagent's own transcript where Pi mirrors it under `session/`.

    Writes `session/subagent-artifacts/<id>_<agent>_1_transcript.jsonl`,
    `session/<parent-session>/<id>/session.jsonl` and
    `session/<parent-session>/<id>/run-1/session.jsonl`, all with the same lines.
    The run table must never read them: the tool result's `usage` already counts
    these turns.

    Returns:
        The three paths written.
    """
    session_dir = run_dir / "session"
    paths = [
        session_dir / "subagent-artifacts" / f"{call_id}_{agent}_1_transcript.jsonl",
        session_dir / parent_session / call_id / "session.jsonl",
        session_dir / parent_session / call_id / "run-1" / "session.jsonl",
    ]
    text = "".join(f"{json.dumps(line)}\n" for line in lines)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return paths


def _message_line(message: JsonObject, message_id: str) -> JsonObject:
    """Wrap `message` in a Pi transcript `type: message` line."""
    return {
        "type": "message",
        "id": message_id,
        "parentId": "m-parent",
        "timestamp": "2026-10-20T10:01:00.000Z",
        "message": message,
    }
