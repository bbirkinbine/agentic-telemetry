"""Reassembling a captured response body (spec 0008: Streaming).

Interface pinned here: `reassemble(body, content_type) -> Reassembled` from
`agentic_telemetry.captures.sse`, where `body` is the decoded response bytes or
None and `content_type` the response's `Content-Type` (parameters allowed).
`Reassembled` has:

- `kind`: `"stream"` (text/event-stream), `"json"` (application/json, passed
  through), `"raw"` (any other type, bytes kept) or `"empty"` (a null body);
- `content` and `reasoning` (str, `""` when none): joined `delta.content`, and
  joined `delta.reasoning_content` / `delta.reasoning`;
- `tool_calls`: a sequence of objects with `index`, `id`, `name` and `arguments`,
  merged by `index` and ordered by it;
- `finish_reason`, `usage`, `timings` (None when absent);
- `incomplete`: True for a stream that never sent `data: [DONE]`;
- `payload` (the parsed JSON for `"json"`) and `raw` (the bytes for `"raw"`).

Streams are synthetic OpenAI chat-completions chunks, as llama.cpp emits them.
"""

from __future__ import annotations

import json

from capture_builders import chunk, sse, text_stream

from agentic_telemetry.captures.sse import reassemble

SSE = "text/event-stream"


def test_content_deltas_are_joined_into_the_final_message() -> None:
    result = reassemble(text_stream("Hel", "lo wor", "ld"), SSE)

    assert result.kind == "stream"
    assert result.content == "Hello world"
    assert result.finish_reason == "stop"
    assert result.incomplete is False


def test_reasoning_content_and_reasoning_deltas_are_kept_apart_from_content() -> None:
    body = sse(
        [
            chunk({"reasoning_content": "think "}),
            chunk({"reasoning": "harder"}),
            chunk({"content": "answer"}),
            chunk({}, finish_reason="stop"),
            "[DONE]",
        ]
    )

    result = reassemble(body, SSE)

    assert result.reasoning == "think harder"
    assert result.content == "answer"


def test_interleaved_tool_calls_merge_split_arguments_by_index() -> None:
    def call(index: int, arguments: str, **head: str) -> dict[str, object]:
        function: dict[str, object] = {"arguments": arguments}
        if "name" in head:
            function["name"] = head["name"]
        item: dict[str, object] = {"index": index, "function": function}
        if "id" in head:
            item.update({"id": head["id"], "type": "function"})
        return {"tool_calls": [item]}

    body = sse(
        [
            chunk(call(0, "", id="call_a", name="read")),
            chunk(call(1, '{"cmd"', id="call_b", name="bash")),
            chunk(call(0, '{"path":')),
            chunk(call(1, ':"ls"}')),
            chunk(call(0, '"a.py"}')),
            chunk({}, finish_reason="tool_calls"),
            "[DONE]",
        ]
    )

    result = reassemble(body, SSE)

    merged = [(c.index, c.id, c.name, c.arguments) for c in result.tool_calls]
    assert merged == [
        (0, "call_a", "read", '{"path":"a.py"}'),
        (1, "call_b", "bash", '{"cmd":"ls"}'),
    ]
    assert result.finish_reason == "tool_calls"


def test_usage_and_timings_come_from_the_final_chunk() -> None:
    usage = {"prompt_tokens": 4321, "completion_tokens": 765, "total_tokens": 5086}
    timings = {"prompt_n": 4321, "predicted_n": 765, "predicted_per_second": 41.5}
    body = sse(
        [
            chunk({"content": "ok"}),
            chunk({}, finish_reason="stop"),
            chunk(usage=usage, timings=timings),
            "[DONE]",
        ]
    )

    result = reassemble(body, SSE)

    assert result.usage == usage
    assert result.timings == timings
    assert result.content == "ok"


def test_done_marker_is_not_parsed_as_a_chunk() -> None:
    result = reassemble(sse([chunk({"content": "x"}), "[DONE]"]), SSE)

    assert result.content == "x"
    assert result.incomplete is False


def test_multi_line_data_fields_join_into_one_event() -> None:
    event = json.dumps(chunk({"content": "joined"}), indent=1).splitlines()
    body = ("".join(f"data: {line}\n" for line in event) + "\ndata: [DONE]\n\n").encode()

    result = reassemble(body, SSE)

    assert result.content == "joined"


def test_crlf_line_endings_are_accepted() -> None:
    body = sse([chunk({"content": "a"}), chunk({"content": "b"}), "[DONE]"], newline="\r\n")

    result = reassemble(body, "text/event-stream; charset=utf-8")

    assert result.content == "ab"
    assert result.incomplete is False


def test_stream_without_done_is_flagged_incomplete() -> None:
    body = sse([chunk({"content": "cut "}), chunk({"content": "off"})])

    result = reassemble(body, SSE)

    assert result.incomplete is True
    assert result.content == "cut off"


def test_non_stream_json_is_passed_through() -> None:
    payload = {
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }

    result = reassemble(json.dumps(payload).encode(), "application/json; charset=utf-8")

    assert result.kind == "json"
    assert result.payload == payload


def test_null_body_is_empty_not_an_error() -> None:
    result = reassemble(None, SSE)

    assert result.kind == "empty"
    assert result.content == ""
    assert list(result.tool_calls) == []


def test_other_content_types_are_left_raw() -> None:
    body = b"\x00\x01 not a chat response"

    result = reassemble(body, "application/octet-stream")

    assert result.kind == "raw"
    assert result.raw == body
    assert result.content == ""
