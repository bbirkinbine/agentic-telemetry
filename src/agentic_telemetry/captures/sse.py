"""Reassembling a captured response into the message the client received.

A streamed response with at least one chat-completions chunk is OpenAI
chat-completions server-sent events, as llama.cpp
emits them: `data:` lines (several per event allowed), events separated by a
blank line, a final `data: [DONE]`. Deltas are joined: `content`,
`reasoning_content` or `reasoning`, and `tool_calls` merged by `index`.
"""

import json
from dataclasses import dataclass, field

JsonObject = dict[str, object]
DONE = "[DONE]"


@dataclass
class ToolCall:
    """One tool call, merged from its streamed pieces."""

    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass
class Reassembled:
    """A response body as one message: `kind` is stream, json, raw or empty."""

    kind: str
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: JsonObject | None = None
    timings: JsonObject | None = None
    incomplete: bool = False
    # Stream events that are not chat chunks, such as a mid-stream `{"error": ...}`.
    other_events: list[str] = field(default_factory=list)
    payload: object = None
    raw: bytes | None = None


def _events(text: str) -> list[str]:
    """Split an SSE body into the joined `data:` payloads of each event."""
    events: list[str] = []
    data: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            if data:
                events.append("\n".join(data))
                data = []
            continue
        if line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)
    if data:
        events.append("\n".join(data))
    return events


def _merge_tool_calls(calls: dict[int, ToolCall], pieces: object) -> None:
    """Merge one delta's `tool_calls` into `calls`, keyed by index."""
    if not isinstance(pieces, list):
        return
    for piece in pieces:
        if not isinstance(piece, dict):
            continue
        index = piece.get("index")
        key = index if isinstance(index, int) else len(calls)
        call = calls.setdefault(key, ToolCall(index=key))
        if isinstance(piece.get("id"), str):
            call.id = str(piece["id"])
        function = piece.get("function")
        if isinstance(function, dict):
            if isinstance(function.get("name"), str):
                call.name = str(function["name"])
            if isinstance(function.get("arguments"), str):
                call.arguments += str(function["arguments"])


def _apply_chunk(result: Reassembled, calls: dict[int, ToolCall], event: JsonObject) -> None:
    """Fold one `chat.completion.chunk` into `result`."""
    if isinstance(event.get("usage"), dict):
        result.usage = event["usage"]  # type: ignore[assignment]
    if isinstance(event.get("timings"), dict):
        result.timings = event["timings"]  # type: ignore[assignment]
    choices = event.get("choices")
    for choice in choices if isinstance(choices, list) else []:
        if not isinstance(choice, dict):
            continue
        if isinstance(choice.get("finish_reason"), str):
            result.finish_reason = choice["finish_reason"]
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        # Some servers send the same text under both keys; count it once.
        for key in ("reasoning_content", "reasoning"):
            if isinstance(delta.get(key), str):
                result.reasoning += delta[key]
                break
        if isinstance(delta.get("content"), str):
            result.content += delta["content"]
        _merge_tool_calls(calls, delta.get("tool_calls"))


def _stream(body: bytes) -> Reassembled:
    """Reassemble a server-sent event stream."""
    result = Reassembled(kind="stream", incomplete=True)
    calls: dict[int, ToolCall] = {}
    chat_chunks = 0
    for payload in _events(body.decode("utf-8", errors="replace")):
        if payload.strip() == DONE:
            result.incomplete = False
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            result.other_events.append(payload)
            continue
        if isinstance(event, dict) and isinstance(event.get("choices"), list):
            chat_chunks += 1
            _apply_chunk(result, calls, event)
        else:
            result.other_events.append(payload)
    if not chat_chunks:
        # Another route's stream (text completions, a non-OpenAI server): keep it raw.
        return Reassembled(kind="raw", raw=body)
    result.tool_calls = [calls[key] for key in sorted(calls)]
    return result


def reassemble(body: bytes | None, content_type: str | None) -> Reassembled:
    """Return a captured response body as one message.

    Args:
        body: The decoded response body, or None when none was captured.
        content_type: The response's `Content-Type`, parameters allowed.
    """
    if body is None:
        return Reassembled(kind="empty")
    media = (content_type or "").split(";", 1)[0].strip().lower()
    if media == "text/event-stream":
        return _stream(body)
    if media == "application/json":
        try:
            return Reassembled(kind="json", payload=json.loads(body))
        except ValueError:
            return Reassembled(kind="raw", raw=body)
    return Reassembled(kind="raw", raw=body)
