"""A fake llama-swap v264 API behind the `ApiFetch` seam (spec 0008).

Contract the tests pin for `agentic_telemetry.captures.llama_swap_api`:

- `ApiFetch = Callable[[str, Mapping[str, str]], bytes]`: `(url, headers) -> body`.
- A fetch signals a transport failure with `LlamaSwapUnreachableError(message)` and
  an HTTP error status with `LlamaSwapQueryError(path, status, detail)`.
- `LlamaSwapApi(base_url, fetch, api_key=None)` turns a 404 on
  `/api/captures/{id}` into `CaptureNotFoundError`, and never lets the base URL
  (host or port) reach an error message.

The fake serves `GET /api/metrics/activity?page=&limit=&sort=id&order=desc` as an
`ActivityPage` (`data`, `page`, `limit`, `total`, `total_pages`) and
`GET /api/captures/{id}` (404 "capture not found" when missing). It can insert
rows between page fetches and script unreachable or 5xx answers. Its unreachable
message quotes the full URL on purpose, so tests can check that nothing logs it.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from capture_builders import BASE_URL, JsonObject

from agentic_telemetry.captures.llama_swap_api import (
    LlamaSwapQueryError,
    LlamaSwapUnreachableError,
)

ACTIVITY_PATH = "/api/metrics/activity"
CAPTURE_PREFIX = "/api/captures/"


@dataclass
class FakeLlamaSwap:
    """In-memory llama-swap: activity rows, captures, and scripted failures."""

    rows: list[JsonObject] = field(default_factory=list)
    captures: dict[int, JsonObject] = field(default_factory=dict)
    insert_after_page: dict[int, list[JsonObject]] = field(default_factory=dict)
    unreachable: bool = False
    server_error: bool = False
    unreachable_capture_ids: set[int] = field(default_factory=set)
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def add(self, row: JsonObject, captured: JsonObject | None = None) -> None:
        """Add one activity row and, when given, its capture."""
        self.rows.append(row)
        if captured is not None:
            self.captures[int(str(row["id"]))] = captured

    def fetch(self, url: str, headers: Mapping[str, str]) -> bytes:
        """Answer one GET the way llama-swap v264 would."""
        self.calls.append((url, dict(headers)))
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}" == BASE_URL, f"unexpected base in {url}"
        if self.unreachable:
            raise LlamaSwapUnreachableError(f"connection refused: {url}")
        if self.server_error:
            raise LlamaSwapQueryError(parts.path, 503, "service unavailable")
        if parts.path == ACTIVITY_PATH:
            return self._activity_page(parse_qs(parts.query))
        if parts.path.startswith(CAPTURE_PREFIX):
            return self._capture(parts.path, url)
        raise LlamaSwapQueryError(parts.path, 404, "not found")

    def activity_urls(self) -> list[str]:
        """Return the activity-page URLs fetched, in order."""
        return [url for url, _ in self.calls if urlsplit(url).path == ACTIVITY_PATH]

    def capture_ids_fetched(self) -> list[int]:
        """Return the capture IDs fetched, in order."""
        return [
            int(urlsplit(url).path.removeprefix(CAPTURE_PREFIX))
            for url, _ in self.calls
            if urlsplit(url).path.startswith(CAPTURE_PREFIX)
        ]

    def _activity_page(self, query: dict[str, list[str]]) -> bytes:
        page = int(query.get("page", ["1"])[0])
        limit = int(query.get("limit", ["25"])[0])
        assert 1 <= limit <= 999, f"llama-swap v264 answers 400 for limit={limit}"
        assert query.get("sort", ["id"]) == ["id"], f"expected sort=id, got {query}"
        assert query.get("order", ["desc"]) == ["desc"], f"expected order=desc, got {query}"
        ordered = sorted(self.rows, key=lambda row: int(str(row["id"])), reverse=True)
        data = ordered[(page - 1) * limit : page * limit]
        body = {
            "data": data,
            "page": page,
            "limit": limit,
            "total": len(ordered),
            "total_pages": math.ceil(len(ordered) / limit) if ordered else 0,
        }
        encoded = json.dumps(body).encode("utf-8")
        self.rows.extend(self.insert_after_page.pop(page, []))
        return encoded

    def _capture(self, path: str, url: str) -> bytes:
        capture_id = int(path.removeprefix(CAPTURE_PREFIX))
        if capture_id in self.unreachable_capture_ids:
            raise LlamaSwapUnreachableError(f"connection reset: {url}")
        if capture_id not in self.captures:
            raise LlamaSwapQueryError(path, 404, "capture not found")
        return json.dumps(self.captures[capture_id]).encode("utf-8")
