"""Summarize one Claude Code transcript (`<session-id>.jsonl`, or a subagent's file).

Claude Code writes one API response as several `assistant` lines, one per
content block, each repeating the same `message.id` and the same usage. Usage,
turns, models and stop reasons are therefore counted once per `message.id`;
tool calls and tool errors are content blocks and are counted per line.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agentic_telemetry.run_table.json_values import JsonObject, as_object, opt_str
from agentic_telemetry.session_table.discovery import delegate_run_id
from agentic_telemetry.session_table.jsonl_lines import JsonlLines
from agentic_telemetry.session_table.row import SessionIdentity, SessionRead
from agentic_telemetry.session_table.tally import SessionTally


@dataclass
class _Response:
    """One deduplicated assistant API response."""

    model: object
    usage: JsonObject | None
    stop_reason: object = None


@dataclass
class _Lines:
    """Identity fields and responses gathered while reading the file."""

    session_ids: list[str] = field(default_factory=list)
    agent_id: str | None = None
    turn_lines: int = 0
    cwd: str | None = None
    version: str | None = None
    git_branch: str | None = None
    responses: dict[str, _Response] = field(default_factory=dict)
    unnamed_responses: list[_Response] = field(default_factory=list)


def read_claude_code(path: Path) -> SessionRead:
    """Read a Claude Code transcript into one session row.

    Args:
        path: The transcript file.

    Returns:
        The row (None when no line carries a session ID), the physical line
        count, and warnings for skipped lines or a file holding two session IDs.

    Raises:
        OSError: The file cannot be read.
    """
    lines = JsonlLines(path)
    tally = SessionTally()
    seen = _Lines()
    for _, record in lines:
        tally.observe_timestamp(record.get("timestamp"))
        _read_line(record, seen, tally)
    warnings = list(lines.warnings)
    for response in [*seen.responses.values(), *seen.unnamed_responses]:
        _count_response(response, tally)
    if not seen.session_ids:
        return SessionRead(None, lines.line_count, warnings)
    distinct = list(dict.fromkeys(seen.session_ids))
    if len(distinct) > 1 and seen.agent_id is None:
        warnings.append(
            f"{path}: holds {len(distinct)} session IDs; keeping the last ({distinct[-1]})"
        )
    is_subagent = seen.agent_id is not None
    identity = SessionIdentity(
        client="claude-code",
        session_id=seen.agent_id if seen.agent_id is not None else seen.session_ids[-1],
        source_path=str(path),
        cwd=seen.cwd,
        client_version=seen.version,
        git_branch=seen.git_branch,
        is_subagent=is_subagent,
        parent_session_id=seen.session_ids[-1] if is_subagent else None,
        run_id=delegate_run_id(path),
    )
    return SessionRead(tally.to_row(identity), lines.line_count, warnings)


def _read_line(record: JsonObject, seen: _Lines, tally: SessionTally) -> None:
    """Record one transcript line's identity fields, activity and response."""
    line_type = record.get("type")
    if line_type == "system":
        if record.get("subtype") == "compact_boundary":
            tally.compactions += 1
        return
    if line_type not in ("user", "assistant"):
        return
    _record_identity(record, seen)
    message = as_object(record.get("message"))
    if line_type == "user":
        _count_user(record, message, tally)
        return
    _record_response(message, seen)
    content = message.get("content") if message else None
    tally.tool_calls += _count_blocks(content, lambda block: block.get("type") == "tool_use")


def _record_identity(record: JsonObject, seen: _Lines) -> None:
    """Keep the session and agent IDs, the first cwd, and the last version and branch."""
    session_id = opt_str(record.get("sessionId"))
    if session_id:
        seen.session_ids.append(session_id)
    seen.turn_lines += 1
    agent_id = opt_str(record.get("agentId"))
    # A subagent file starts as a sidechain; a stray sidechain line later in a
    # main file does not re-key the whole session.
    if seen.turn_lines == 1 and record.get("isSidechain") is True and agent_id:
        seen.agent_id = agent_id
    if seen.cwd is None:
        seen.cwd = opt_str(record.get("cwd"))
    seen.version = opt_str(record.get("version")) or seen.version
    seen.git_branch = opt_str(record.get("gitBranch")) or seen.git_branch


def _record_response(message: JsonObject | None, seen: _Lines) -> None:
    """Fold one assistant line into its response, keyed by `message.id`."""
    if message is None:
        seen.unnamed_responses.append(_Response(model=None, usage=None))
        return
    stop_reason = message.get("stop_reason")
    message_id = opt_str(message.get("id"))
    if message_id is None:
        response = _Response(message.get("model"), as_object(message.get("usage")), stop_reason)
        seen.unnamed_responses.append(response)
        return
    known = seen.responses.get(message_id)
    if known is None:
        known = _Response(message.get("model"), as_object(message.get("usage")))
        seen.responses[message_id] = known
    if stop_reason is not None:
        known.stop_reason = stop_reason


def _count_response(response: _Response, tally: SessionTally) -> None:
    """Add one deduplicated response's turn, stop reason and usage."""
    # `message.model` is the API response's own model field: the model that answered.
    tally.count_assistant_turn(response.model, served_model=response.model)
    tally.count_stop_reason(response.stop_reason)
    usage = response.usage
    if usage is not None:
        tally.add_usage(
            tokens_in=usage.get("input_tokens"),
            tokens_cache_read=usage.get("cache_read_input_tokens"),
            tokens_cache_write=usage.get("cache_creation_input_tokens"),
            tokens_out=usage.get("output_tokens"),
        )


def _count_user(record: JsonObject, message: JsonObject | None, tally: SessionTally) -> None:
    """Count tool errors in a user line, and the line itself when it is a human prompt."""
    content = message.get("content") if message else None
    tally.tool_errors += _count_blocks(
        content, lambda block: block.get("type") == "tool_result" and block.get("is_error") is True
    )
    if record.get("isMeta") is True or record.get("isCompactSummary") is True:
        return
    is_text = isinstance(content, str) and bool(content)
    if is_text or _count_blocks(content, lambda block: block.get("type") == "text"):
        tally.user_prompts += 1


def _count_blocks(content: object, matches: Callable[[JsonObject], bool]) -> int:
    """Return how many content blocks in a list satisfy `matches`."""
    if not isinstance(content, list):
        return 0
    blocks = [as_object(block) for block in content]
    return sum(1 for block in blocks if block is not None and matches(block))
