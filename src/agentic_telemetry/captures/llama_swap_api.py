"""The one network seam for captures: read-only GETs against llama-swap's API.

Routes, parameters and auth are those of llama-swap v264 (MIT),
`internal/server/apigroup.go` and `internal/server/auth.go`, at
https://github.com/mostlygeek/llama-swap/tree/v264 , retrieved 2026-10-22
(spec 0008, "Step 0 results"):

- `GET /api/metrics/activity` takes `page` (>= 1), `limit` (1 to 999), `sort`
  and `order`, and answers an `ActivityPage` (`data`, `page`, `limit`, `total`,
  `total_pages`).
- `GET /api/captures/{id}` answers 404 "capture not found" when the capture
  cache no longer holds the call.
- With `apiKeys` configured, a key is accepted as `Authorization: Bearer`.

`ApiFetch` is the seam: tests pass a fake, so nothing here needs a listening
port. Error messages name the API path only, never the host, port or key.
"""

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit

ApiFetch = Callable[[str, Mapping[str, str]], bytes]

ACTIVITY_PATH = "/api/metrics/activity"
CAPTURE_PATH = "/api/captures/"
PAGE_LIMIT = 200
DEFAULT_TIMEOUT_SECONDS = 10.0

JsonObject = dict[str, object]


class LlamaSwapUnreachableError(RuntimeError):
    """llama-swap could not be reached, or the transfer broke off."""


class LlamaSwapQueryError(RuntimeError):
    """One request was answered with an HTTP error status or an unusable body."""

    def __init__(self, path: str, status: int, detail: str) -> None:
        """Record the API path, the HTTP status and what went wrong there."""
        self.path = path
        self.status = status
        self.detail = detail
        super().__init__(f"{path}: HTTP {status}: {detail}")


class CaptureNotFoundError(LlamaSwapQueryError):
    """The capture is gone from llama-swap's cache (evicted, or lost in a restart)."""


def urlopen_api_fetch(url: str, headers: Mapping[str, str]) -> bytes:
    """Return the body of a GET to `url` sent with `headers`.

    Raises:
        LlamaSwapUnreachableError: A non-HTTP scheme or any transport failure.
        LlamaSwapQueryError: The server answered with an HTTP error status.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise LlamaSwapUnreachableError(f"{parts.path}: URL must use http or https")
    request = urllib.request.Request(url, headers=dict(headers), method="GET")  # noqa: S310
    try:
        # The scheme is checked above; the URL is the one the operator configured.
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:  # noqa: S310
            body: bytes = response.read()
        return body
    except urllib.error.HTTPError as error:
        raise LlamaSwapQueryError(parts.path, error.code, str(error.reason)) from error
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
        ValueError,
    ) as error:
        # HTTPException: a port that speaks another protocol, or a cut-off body.
        raise LlamaSwapUnreachableError(f"{parts.path}: {type(error).__name__}") from error


def _scrub(base_url: str, text: str) -> str:
    """Return `text` with the base URL's host and port replaced."""
    parts = urlsplit(base_url)
    try:
        port = str(parts.port) if parts.port is not None else ""
    except ValueError:
        port = ""
    for token in (parts.netloc, parts.hostname or "", port):
        if token:
            text = text.replace(token, "[llama-swap]")
    return text


class LlamaSwapApi:
    """Typed reads of llama-swap's activity list and captures."""

    def __init__(self, base_url: str, fetch: ApiFetch, api_key: str | None = None) -> None:
        """Remember where llama-swap is, how to reach it, and the optional API key."""
        self._base_url = base_url.rstrip("/")
        self._fetch = fetch
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def activity_page(self, page: int) -> JsonObject:
        """Return one page of activity rows, newest id first."""
        query = f"page={page}&limit={PAGE_LIMIT}&sort=id&order=desc"
        return self._get_json(ACTIVITY_PATH, query)

    def capture(self, call_id: int) -> JsonObject:
        """Return the capture of one call.

        Raises:
            CaptureNotFoundError: llama-swap answered 404; the capture is gone.
        """
        path = f"{CAPTURE_PATH}{call_id}"
        try:
            return self._get_json(path, "")
        except LlamaSwapQueryError as error:
            if error.status == 404:
                raise CaptureNotFoundError(path, 404, error.detail) from error
            raise

    def _get_json(self, path: str, query: str) -> JsonObject:
        """GET `path?query` and parse a JSON object; errors name the path only."""
        url = f"{self._base_url}{path}" + (f"?{query}" if query else "")
        try:
            body = self._fetch(url, self._headers)
        except LlamaSwapUnreachableError as error:
            raise LlamaSwapUnreachableError(
                f"{path}: unreachable: {_scrub(self._base_url, str(error))}"
            ) from error
        except LlamaSwapQueryError as error:
            detail = _scrub(self._base_url, error.detail)
            if isinstance(error, CaptureNotFoundError):
                raise CaptureNotFoundError(path, error.status, detail) from error
            raise LlamaSwapQueryError(path, error.status, detail) from error
        try:
            parsed = json.loads(body)
        except ValueError as error:
            raise LlamaSwapQueryError(path, 200, "body is not JSON") from error
        if not isinstance(parsed, dict):
            raise LlamaSwapQueryError(path, 200, "body is not a JSON object")
        return parsed
