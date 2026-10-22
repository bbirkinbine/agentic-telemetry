"""The stdlib HTTP fetch behind `captures-pull` (spec 0008: No silent loss).

The harvester's `evicted` count depends on a real 404 becoming
`CaptureNotFoundError`, and its logs on errors naming the path, never the host.
Every server here is a loopback `http.server` on an ephemeral 127.0.0.1 port.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agentic_telemetry.captures.llama_swap_api import (
    CaptureNotFoundError,
    LlamaSwapApi,
    LlamaSwapQueryError,
    LlamaSwapUnreachableError,
    urlopen_api_fetch,
)

BODY = b'{"id": 7, "req_path": "/v1/chat/completions"}'


class LoopbackServer(HTTPServer):
    """An HTTP server that answers every GET with one canned status and body."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.reply: tuple[int, bytes] = (200, BODY)
        self.seen: list[tuple[str, str | None]] = []

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    server: LoopbackServer

    def do_GET(self) -> None:
        self.server.seen.append((self.path, self.headers.get("Authorization")))
        status, body = self.server.reply
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Keep request logs out of the test output."""


@pytest.fixture
def loopback() -> Iterator[LoopbackServer]:
    server = LoopbackServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def closed_port() -> int:
    """Return a loopback port that was bound and then released, so nothing listens."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def test_returns_the_body_and_sends_the_headers(loopback: LoopbackServer) -> None:
    body = urlopen_api_fetch(f"{loopback.base}/api/captures/7", {"Authorization": "Bearer k"})

    assert body == BODY
    assert loopback.seen == [("/api/captures/7", "Bearer k")]


def test_a_real_404_on_a_capture_becomes_capture_not_found(loopback: LoopbackServer) -> None:
    loopback.reply = (404, b"capture not found")

    with pytest.raises(CaptureNotFoundError) as raised:
        LlamaSwapApi(loopback.base, urlopen_api_fetch).capture(7)

    assert raised.value.status == 404
    assert "127.0.0.1" not in str(raised.value)


def test_a_server_error_is_a_query_error_naming_the_path(loopback: LoopbackServer) -> None:
    loopback.reply = (503, b"busy")

    with pytest.raises(LlamaSwapQueryError) as raised:
        LlamaSwapApi(loopback.base, urlopen_api_fetch).activity_page(1)

    assert not isinstance(raised.value, CaptureNotFoundError)
    assert raised.value.status == 503
    assert "/api/metrics/activity" in str(raised.value)
    assert "127.0.0.1" not in str(raised.value)


def test_refused_connection_is_unreachable_without_host_or_port() -> None:
    port = closed_port()

    with pytest.raises(LlamaSwapUnreachableError) as raised:
        LlamaSwapApi(f"http://127.0.0.1:{port}", urlopen_api_fetch).activity_page(1)

    message = str(raised.value)
    assert "/api/metrics/activity" in message
    assert "127.0.0.1" not in message
    assert str(port) not in message


def test_a_port_that_does_not_speak_http_is_unreachable_not_a_traceback() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port: int = listener.getsockname()[1]

        def answer_garbage() -> None:
            connection, _ = listener.accept()
            with connection:
                connection.recv(4096)
                connection.sendall(b"SSH-2.0-not-http\r\n\r\n")

        thread = threading.Thread(target=answer_garbage, daemon=True)
        thread.start()
        with pytest.raises(LlamaSwapUnreachableError) as raised:
            urlopen_api_fetch(f"http://127.0.0.1:{port}/api/captures/1", {})
        thread.join(timeout=5)

    assert "127.0.0.1" not in str(raised.value)


def test_non_http_scheme_is_refused() -> None:
    with pytest.raises(LlamaSwapUnreachableError):
        urlopen_api_fetch("file:///etc/hosts", {})
