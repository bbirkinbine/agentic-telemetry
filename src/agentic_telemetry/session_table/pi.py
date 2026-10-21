"""Summarize one Pi session transcript (`<timestamp>_<session-id>.jsonl`) into a session row.

The first line is the session header (`type: "session"`, `id`, `cwd`, and
`parentSession` for a child session). Usage, models and stop reasons come from
assistant `message` entries; tool calls are their `toolCall` content items.
"""

from pathlib import Path, PurePosixPath

from agentic_telemetry.run_table.json_values import JsonObject, as_object, opt_str
from agentic_telemetry.session_table.discovery import delegate_run_id
from agentic_telemetry.session_table.jsonl_lines import JsonlLines
from agentic_telemetry.session_table.row import SessionIdentity, SessionRead
from agentic_telemetry.session_table.tally import SessionTally


def read_pi(path: Path) -> SessionRead:
    """Read a Pi transcript into one session row.

    Args:
        path: The transcript file.

    Returns:
        The row (None when the header has no session ID), the physical line
        count, and one warning per skipped line.

    Raises:
        OSError: The file cannot be read.
    """
    lines = JsonlLines(path)
    tally = SessionTally()
    header: JsonObject | None = None
    for _, record in lines:
        tally.observe_timestamp(record.get("timestamp"))
        if header is None and record.get("type") == "session":
            header = record
            continue
        _count_entry(record, tally)
    session_id = opt_str(header.get("id")) if header else None
    if header is None or not session_id:
        return SessionRead(None, lines.line_count, lines.warnings)
    parent_path = opt_str(header.get("parentSession"))
    parent = _parent_session_id(parent_path)
    identity = SessionIdentity(
        client="pi",
        session_id=session_id,
        source_path=str(path),
        cwd=opt_str(header.get("cwd")),
        is_subagent=_is_subagent_file(path, parent_path),
        parent_session_id=parent,
        run_id=delegate_run_id(path),
        provider=tally.primary_provider,
    )
    return SessionRead(tally.to_row(identity), lines.line_count, lines.warnings)


def _count_entry(record: JsonObject, tally: SessionTally) -> None:
    """Add one non-header entry to the tally."""
    entry_type = record.get("type")
    if entry_type == "compaction":
        tally.compactions += 1
        return
    if entry_type != "message":
        return
    message = as_object(record.get("message"))
    if message is None:
        return
    role = message.get("role")
    content = message.get("content")
    if role == "assistant":
        _count_assistant(message, tally)
    elif role == "toolResult" and message.get("isError") is True:
        tally.tool_errors += 1
    elif role == "user" and ((isinstance(content, str) and content) or _has_item(content, "text")):
        tally.user_prompts += 1


def _count_assistant(message: JsonObject, tally: SessionTally) -> None:
    """Add one assistant message: turn, model, usage, stop reason and tool calls."""
    # Pi records `responseModel` only when the answering model differs from the
    # requested one, so `model` is the served model otherwise. Source: Pi 1.3.0
    # `docs/message-types.md` (MIT), retrieved 2026-10-21.
    served = opt_str(message.get("responseModel")) or message.get("model")
    tally.count_assistant_turn(message.get("model"), message.get("provider"), served)
    tally.count_stop_reason(message.get("stopReason"))
    usage = as_object(message.get("usage"))
    if usage is not None:
        tally.add_usage(
            tokens_in=usage.get("input"),
            tokens_cache_read=usage.get("cacheRead"),
            tokens_cache_write=usage.get("cacheWrite"),
            tokens_out=usage.get("output"),
            tokens_reasoning=usage.get("reasoning"),
        )
    content = message.get("content")
    if isinstance(content, list):
        tally.tool_calls += sum(1 for item in content if _item_type(item) == "toolCall")


def _has_item(content: object, item_type: str) -> bool:
    """Return True when a content list holds an item of `item_type`."""
    return isinstance(content, list) and any(_item_type(item) == item_type for item in content)


def _item_type(item: object) -> object:
    """Return a content item's `type`, or None when the item is not an object."""
    item_object = as_object(item)
    return item_object.get("type") if item_object is not None else None


def _is_subagent_file(path: Path, parent_path: str | None) -> bool:
    """True when the file sits under a directory named after its parent's file stem.

    Pi records `parentSession` for `/fork` and `/clone` too, but those sessions sit
    beside their parent. A subagent extension writes its children under
    `<parent-stem>/<id>/run-<n>/` (all 97 child sessions observed on the owner's
    machine).
    """
    if not parent_path:
        return False
    parent_stem = PurePosixPath(parent_path).name.removesuffix(".jsonl")
    if not parent_stem:
        return False
    return any(ancestor.name == parent_stem for ancestor in path.parents)


def _parent_session_id(path_text: str | None) -> str | None:
    """Return the parent's session ID: its file stem after the last `_`."""
    if not path_text:
        return None
    stem = PurePosixPath(path_text).name.removesuffix(".jsonl")
    return stem.rsplit("_", 1)[-1] or None
