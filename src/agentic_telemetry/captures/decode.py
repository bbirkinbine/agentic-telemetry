"""Reading a stored capture: base64 bodies, header lookup, the requested model.

llama-swap v264 (MIT, `internal/server/captures.go`, retrieved 2026-10-22)
stores bodies as Go `[]byte`, which `encoding/json` writes as base64 or null,
and headers as one value per Go-canonical key, so lookups ignore case.
"""

import base64
import binascii
import json
from collections.abc import Mapping

JsonObject = dict[str, object]

# The headers a client may carry its session ID in, in the order they are trusted.
SESSION_HEADERS = ("x-session-id", "x-session-affinity", "x-client-request-id")


def decode_body(value: object) -> bytes | None:
    """Return a base64 body as bytes, or None for a null or unreadable one."""
    if not isinstance(value, str):
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def json_body(value: object) -> object | None:
    """Return a base64 body parsed as JSON, or None when it is not JSON."""
    body = decode_body(value)
    if body is None:
        return None
    try:
        parsed: object = json.loads(body)
    except ValueError:
        return None
    return parsed


def header(headers: object, name: str) -> str | None:
    """Return the value of header `name` in any case, or None."""
    if not isinstance(headers, Mapping):
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == wanted and isinstance(value, str):
            return value
    return None


def session_header(headers: object) -> str | None:
    """Return the first non-empty session header value, in `SESSION_HEADERS` order."""
    for name in SESSION_HEADERS:
        value = header(headers, name)
        if value:
            return value
    return None


def request_model(capture: object) -> str | None:
    """Return the `model` field of a capture's request body, or None."""
    if not isinstance(capture, Mapping):
        return None
    body = json_body(capture.get("req_body"))
    if isinstance(body, dict):
        model = body.get("model")
        if isinstance(model, str) and model:
            return model
    return None
