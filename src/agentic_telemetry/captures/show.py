"""Text views of joined calls: a session's calls, a day's calls, one call in full.

Times are call ends in UTC. `src` (the client address llama-swap recorded) is
shown: captures stay in the owner's lab (spec 0008 amendment, 2026-10-22).
"""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime

from agentic_telemetry.captures.activity import record_end, token_count
from agentic_telemetry.captures.decode import decode_body, header, json_body
from agentic_telemetry.captures.join import ClaimedCall
from agentic_telemetry.captures.session_windows import SessionWindow
from agentic_telemetry.captures.sse import Reassembled, reassemble


def _activity(call: ClaimedCall) -> dict[str, object]:
    """Return a claimed call's activity object."""
    activity = call.record.get("activity")
    return dict(activity) if isinstance(activity, dict) else {}


def ended(call: ClaimedCall) -> datetime:
    """Return when a claimed call ended, in UTC; the sort key of every view."""
    return record_end(call.record)


def _count(value: int | None) -> str:
    """Render a token count, `-` when unknown."""
    return "-" if value is None else str(value)


def call_line(number: int, call: ClaimedCall, *, with_sessions: bool) -> str:
    """Return one list line: number, time, model, tokens, duration, status, src, flags."""
    activity = _activity(call)
    parts = [
        f"{number:>4}",
        ended(call).strftime("%H:%M:%S"),
        str(activity.get("model", "?")),
        f"in={_count(token_count(activity, 'input_tokens'))}",
        f"out={_count(token_count(activity, 'output_tokens'))}",
        f"{activity.get('duration_ms', '?')} ms",
        str(call.record.get("capture_status", "?")),
        f"src={activity.get('src', '?')}",
    ]
    if with_sessions:
        parts.append("sessions=" + ",".join(call.session_ids) if call.session_ids else "unclaimed")
    parts.extend(call.flags)
    return "  ".join(parts)


def session_header_line(window: SessionWindow, own: int) -> str:
    """Return the heading of a session view."""
    span = "?"
    if window.start is not None and window.end is not None:
        span = f"{window.start:%Y-%m-%d %H:%M:%S}-{window.end:%H:%M:%S} UTC"
    run = f", run {window.run_id}" if window.run_id else ""
    return f"session {window.session_id} ({window.client}{run}), {span}: {own} calls"


def session_view(
    window: SessionWindow, own: Sequence[ClaimedCall], children: Mapping[str, int]
) -> str:
    """Return a session's own calls in order, then one line per child session."""
    lines = [session_header_line(window, len(own))]
    lines += [call_line(number, call, with_sessions=False) for number, call in enumerate(own, 1)]
    lines += [f"child {child}: {count} calls" for child, count in sorted(children.items())]
    return "\n".join(lines)


def date_view(day: str, calls: Sequence[ClaimedCall]) -> str:
    """Return every call of one UTC day with the sessions that claim it."""
    lines = [f"calls on {day} (UTC): {len(calls)}"]
    lines += [call_line(number, call, with_sessions=True) for number, call in enumerate(calls, 1)]
    return "\n".join(lines)


def _response_lines(result: Reassembled) -> list[str]:
    """Return the reassembled response as text lines."""
    if result.kind == "empty":
        return ["response: none captured"]
    if result.kind == "json":
        return ["response (json):", json.dumps(result.payload, indent=2, ensure_ascii=False)]
    if result.kind == "raw":
        raw = result.raw or b""
        return [
            f"response (raw, {len(raw)} bytes; not a chat-completions stream or JSON):",
            raw.decode("utf-8", errors="replace"),
        ]
    state = "incomplete, no [DONE]" if result.incomplete else "complete"
    lines = [f"response (stream, {state}), finish_reason={result.finish_reason}"]
    if result.reasoning:
        lines += ["reasoning:", result.reasoning]
    lines += ["content:", result.content]
    for tool in result.tool_calls:
        lines.append(f"tool call [{tool.index}] {tool.id} {tool.name}: {tool.arguments}")
    if result.usage is not None:
        lines.append("usage: " + json.dumps(result.usage))
    if result.timings is not None:
        lines.append("timings: " + json.dumps(result.timings))
    if result.other_events:
        lines += ["other events:", *result.other_events]
    return lines


def call_detail(number: int, call: ClaimedCall) -> str:
    """Return one call in full: its activity, request and reassembled response."""
    activity = _activity(call)
    owners = ",".join(call.session_ids) or "unclaimed"
    lines = [
        f"call {number}: ended {ended(call):%Y-%m-%d %H:%M:%S} UTC, "
        f"{activity.get('duration_ms', '?')} ms",
        f"model: {activity.get('model', '?')}  path: {activity.get('req_path', '?')}  "
        f"HTTP {activity.get('resp_status_code', '?')}",
        f"tokens: in={_count(token_count(activity, 'input_tokens'))} "
        f"out={_count(token_count(activity, 'output_tokens'))} "
        f"cache={_count(token_count(activity, 'cache_tokens'))}",
        f"src: {activity.get('src', '?')}",
        f"sessions: {owners}" + (f"  flags: {','.join(call.flags)}" if call.flags else ""),
        f"capture: {call.record.get('capture_status', '?')}",
    ]
    capture = call.record.get("capture")
    if not isinstance(capture, dict):
        return "\n".join(lines)
    lines.append("request headers: " + json.dumps(capture.get("req_headers"), sort_keys=True))
    request = json_body(capture.get("req_body"))
    if request is not None:
        lines += ["request:", json.dumps(request, indent=2, ensure_ascii=False)]
    else:
        raw = decode_body(capture.get("req_body")) or b""
        lines += ["request (not JSON):", raw.decode("utf-8", errors="replace")]
    content_type = header(capture.get("resp_headers"), "content-type")
    result = reassemble(decode_body(capture.get("resp_body")), content_type)
    lines += _response_lines(result)
    return "\n".join(lines)
