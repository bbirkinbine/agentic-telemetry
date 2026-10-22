"""The one network seam: a read-only stdlib GET against VictoriaMetrics.

The request shapes are those of "API examples", VictoriaMetrics (Apache-2.0),
https://docs.victoriametrics.com/victoriametrics/url-examples/ , retrieved
2026-10-22. No dependency is added for them: two read-only GETs are covered by
`urllib.request` (spec 0005, "No new dependency").

`Fetch` is the seam: every caller takes it as an argument and tests pass a fake,
so nothing in `src/` monkeypatches `urlopen`.

Error messages name the URL *path* only. The base URL carries a host and port,
and a warning that reaches stdout or a run table must not carry either.
"""

import http.client
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urlsplit

Fetch = Callable[[str], bytes]

DEFAULT_TIMEOUT_SECONDS = 10.0


class MetricsUnreachableError(RuntimeError):
    """The metrics endpoint could not be reached, or is not an HTTP endpoint at all."""


class MetricsQueryError(RuntimeError):
    """One request was answered with an HTTP error status or an unusable body."""

    def __init__(self, path: str, detail: str) -> None:
        """Record the API path the failure belongs to and what went wrong there."""
        self.path = path
        self.detail = detail
        super().__init__(f"{path}: {detail}")


def urlopen_fetch(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> bytes:
    """Return the response body of a GET to `url`.

    Args:
        url: A VictoriaMetrics URL; its scheme must be http or https.
        timeout: Seconds to wait for the response.

    Returns:
        The response body bytes.

    Raises:
        MetricsUnreachableError: A non-HTTP scheme, a refused or timed-out
            connection, or any other transport failure.
        MetricsQueryError: The server answered with an HTTP error status.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise MetricsUnreachableError(
            f"metrics URL must use http or https, got scheme {parts.scheme or 'none'!r}"
        )
    try:
        # The scheme is checked above; this is a GET against a URL the operator
        # named on the command line, and the only thing read from it is the body.
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            body: bytes = response.read()
        return body
    except urllib.error.HTTPError as error:
        raise MetricsQueryError(parts.path, f"HTTP {error.code}") from error
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
        ValueError,
    ) as error:
        # HTTPException covers a port that speaks another protocol or a cut-off
        # body; ValueError covers http.client.InvalidURL.
        raise MetricsUnreachableError(f"{parts.path}: {_without_host(url, str(error))}") from error


def _without_host(url: str, text: str) -> str:
    """Return `text` with the URL's host and port replaced, for outbound messages.

    Transport errors quote their target on some platforms; the path is what a
    reader (or a committed run table) is allowed to see. `urlsplit` parses IPv6
    literals and userinfo correctly, unlike splitting the netloc on a colon.
    """
    parts = urlsplit(url)
    try:
        port = str(parts.port) if parts.port is not None else ""
    except ValueError:
        port = ""
    for token in (parts.netloc, parts.hostname or "", port):
        if token:
            text = text.replace(token, "[metrics host]")
    return text
