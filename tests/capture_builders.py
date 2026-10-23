"""Synthetic llama-swap activity rows, captures, SSE streams and sessions (spec 0008).

Every value is made up. Shapes follow llama-swap v264 (MIT), read in-session on
2026-10-22 (spec 0008 "Step 0 results"): activity rows from
`internal/store/activity.go`, captures from `internal/server/captures.go` (byte
fields base64 or null, one value per Go-canonical header key). Hosts and client
addresses are RFC 2606 / RFC 5737 placeholders. `SENTINEL` stands in for prompt
content so tests can check that log lines never carry it.

The harvested record shape is the plan's:
`{"activity", "capture", "capture_status", "harvested_at", "epoch"}`.
Sessions are written with the real session-table writer, so their columns and
types are the ones `session-table` produces.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import zstandard

from agentic_telemetry.session_table.parquet_writer import write_session_table
from agentic_telemetry.session_table.row import SessionRow

JsonObject = dict[str, object]

BASE_URL = "http://gpu.example:8080"
HOST_FRAGMENTS = ("gpu.example", "8080")
SRC = "192.0.2.10"
SENTINEL = "CAPTURE-CONTENT-SENTINEL"
API_KEY = "fake-key-for-tests"
QWEN = "qwen3.8-27b-ud-q8_k_xl"
FLASH = "qwen3.8-flash-next-unsloth-q8_0"
ALIAS_QWEN = "local-qwen38-27b"
DAY = datetime(2026, 10, 22, tzinfo=UTC)


def at(hour: int, minute: int = 0, second: int = 0, micro: int = 0) -> datetime:
    """Return an aware UTC instant on 2026-10-22."""
    return DAY.replace(hour=hour, minute=minute, second=second, microsecond=micro)


def go_time(instant: datetime, nanos: int = 0) -> str:
    """Render `instant` as Go RFC3339Nano: up to 9 fraction digits, trailing zeros dropped.

    `nanos` adds sub-microsecond digits (0-999). The offset is `Z` for UTC, else `+HH:MM`.
    """
    fraction = instant.microsecond * 1000 + nanos
    text = instant.strftime("%Y-%m-%dT%H:%M:%S")
    if fraction:
        text += f".{fraction:09d}".rstrip("0")
    offset = instant.utcoffset() or timedelta(0)
    if not offset:
        return text + "Z"
    sign = "-" if offset < timedelta(0) else "+"
    minutes = abs(int(offset.total_seconds())) // 60
    return f"{text}{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def activity_row(
    row_id: int,
    timestamp: datetime | str,
    *,
    model: str = QWEN,
    has_capture: bool = True,
    duration_ms: int = 1500,
    input_tokens: int = 100,
    output_tokens: int = 20,
    src: str = SRC,
    req_path: str = "/v1/chat/completions",
    status: int = 200,
) -> JsonObject:
    """Return one activity row as `GET /api/metrics/activity` serves it."""
    stamp = timestamp if isinstance(timestamp, str) else go_time(timestamp)
    return {
        "id": row_id,
        "timestamp": stamp,
        "src": src,
        "model": model,
        "req_path": req_path,
        "resp_content_type": "text/event-stream",
        "resp_status_code": status,
        "tokens": {
            "cache_tokens": 0,
            "draft_tokens": 0,
            "draft_acc_tokens": 0,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "prompt_per_second": 812.5,
            "tokens_per_second": 41.25,
        },
        "duration_ms": duration_ms,
        "has_capture": has_capture,
        "error_msg": "",
        "metadata": {},
    }


def b64(data: bytes | None) -> str | None:
    """Return `data` base64-encoded as Go's `encoding/json` does for `[]byte`, or None."""
    return None if data is None else base64.b64encode(data).decode("ascii")


def chat_request(model: str = ALIAS_QWEN, *, stream: bool = True) -> JsonObject:
    """Return a chat-completions request body carrying the sentinel system prompt."""
    return {
        "model": model,
        "stream": stream,
        "temperature": 0.6,
        "messages": [
            {"role": "system", "content": f"{SENTINEL} system prompt"},
            {"role": "user", "content": "list the files"},
        ],
        "tools": [{"type": "function", "function": {"name": "bash", "parameters": {}}}],
    }


def capture(
    capture_id: int,
    *,
    request: Mapping[str, object] | None = None,
    response: bytes | None = b"data: [DONE]\n\n",
    content_type: str = "text/event-stream",
    session_headers: Mapping[str, str] | None = None,
) -> JsonObject:
    """Return one capture as `GET /api/captures/{id}` serves it."""
    body = json.dumps(dict(request) if request is not None else chat_request()).encode()
    req_headers = {
        "Authorization": "[REDACTED]",
        "Content-Type": "application/json",
        **(session_headers or {}),
    }
    return {
        "id": capture_id,
        "req_path": "/v1/chat/completions",
        "req_headers": req_headers,
        "req_body": b64(body),
        "resp_headers": {"Content-Type": content_type},
        "resp_body": b64(response),
    }


def sse(events: Sequence[Mapping[str, object] | str], *, newline: str = "\n") -> bytes:
    """Return an SSE body: one `data:` event per item (mappings as JSON, str raw)."""
    parts = [
        f"data: {item if isinstance(item, str) else json.dumps(item)}{newline}{newline}"
        for item in events
    ]
    return "".join(parts).encode("utf-8")


def chunk(
    delta: Mapping[str, object] | None = None,
    *,
    finish_reason: str | None = None,
    usage: Mapping[str, object] | None = None,
    timings: Mapping[str, object] | None = None,
) -> JsonObject:
    """Return one `chat.completion.chunk`; with only usage/timings, `choices` is empty."""
    event: JsonObject = {"object": "chat.completion.chunk", "model": QWEN}
    if delta is None and finish_reason is None:
        event["choices"] = []
    else:
        choice = {"index": 0, "delta": dict(delta or {}), "finish_reason": finish_reason}
        event["choices"] = [choice]
    if usage is not None:
        event["usage"] = dict(usage)
    if timings is not None:
        event["timings"] = dict(timings)
    return event


def text_stream(*pieces: str) -> bytes:
    """Return a complete stream whose content deltas are `pieces`, ending with `[DONE]`."""
    events: list[Mapping[str, object] | str] = [chunk({"role": "assistant", "content": ""})]
    events += [chunk({"content": piece}) for piece in pieces]
    events += [chunk({}, finish_reason="stop"), "[DONE]"]
    return sse(events)


def record(
    activity: Mapping[str, object],
    captured: Mapping[str, object] | None,
    *,
    status: str = "saved",
    epoch: int = 0,
) -> JsonObject:
    """Return one harvested record in the plan's shape."""
    return {
        "activity": dict(activity),
        "capture": dict(captured) if captured is not None else None,
        "capture_status": status,
        "harvested_at": "2026-10-22T23:00:00.000+00:00",
        "epoch": epoch,
    }


def write_capture_file(path: Path, records: Sequence[Mapping[str, object]]) -> Path:
    """Write `records` as one zstd frame of JSON lines, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(item) + "\n" for item in records)
    path.write_bytes(zstandard.ZstdCompressor().compress(text.encode("utf-8")))
    return path


def read_capture_lines(path: Path) -> list[JsonObject]:
    """Decompress every frame of a capture file and parse each JSON line."""
    with path.open("rb") as handle:
        reader = zstandard.ZstdDecompressor().stream_reader(handle, read_across_frames=True)
        text = reader.read().decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def session_row(
    session_id: str,
    started: datetime,
    ended: datetime,
    *,
    models: Sequence[str] = (ALIAS_QWEN,),
    served: Sequence[str] = (),
    parent: str | None = None,
    run_id: str | None = None,
    client: str = "pi",
) -> SessionRow:
    """Return a session-table row; timestamps are stored as the session-table writer does."""
    return SessionRow(
        client=client,
        session_id=session_id,
        source_path=f"/workspace/sessions/{session_id}.jsonl",
        project="alpha-project",
        cwd="/workspace/alpha-project",
        client_version="1.3.0",
        git_branch=None,
        is_subagent=parent is not None,
        parent_session_id=parent,
        run_id=run_id,
        started_at=started.isoformat(timespec="milliseconds"),
        ended_at=ended.isoformat(timespec="milliseconds"),
        span_seconds=(ended - started).total_seconds(),
        provider="local",
        models=tuple(models),
        primary_model=models[0] if models else None,
        served_models=tuple(served),
        primary_served_model=served[0] if served else None,
        tokens_in=1000,
        tokens_cache_read=None,
        tokens_cache_write=None,
        tokens_out=100,
        tokens_reasoning=None,
        assistant_turns=3,
        user_prompts=1,
        tool_calls=2,
        tool_errors=0,
        compactions=0,
        stop_reasons=None,
    )


def write_sessions(path: Path, rows: Sequence[SessionRow]) -> Path:
    """Write a session-table Parquet with the real writer and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_session_table(rows, path)
    return path
