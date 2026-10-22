"""The stdlib HTTP fetch behind `--metrics-url` (spec 0005, No new dependency).

Every server here is a loopback `http.server` on an ephemeral 127.0.0.1 port.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from agentic_telemetry.server_metrics.http_fetch import (
    MetricsQueryError,
    MetricsUnreachableError,
    urlopen_fetch,
)

BODY = b'{"status":"success","data":["qwen3.8-27b-ud-q8_k_xl"]}'


class LoopbackServer(HTTPServer):
    """An HTTP server that answers every GET with one canned status and body."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.reply: tuple[int, bytes] = (200, BODY)
        self.seen_paths: list[str] = []

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    server: LoopbackServer

    def do_GET(self) -> None:
        self.server.seen_paths.append(self.path)
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


def test_returns_the_response_body_bytes(loopback: LoopbackServer) -> None:
    url = f"{loopback.base}/api/v1/label/model_id/values?start=1&end=2"

    assert urlopen_fetch(url) == BODY


def test_sends_the_path_and_query_string_unchanged(loopback: LoopbackServer) -> None:
    urlopen_fetch(f"{loopback.base}/api/v1/query?query=up&time=1792663300&nocache=1")

    assert loopback.seen_paths == ["/api/v1/query?query=up&time=1792663300&nocache=1"]


def test_refused_connection_raises_unreachable_naming_the_path_not_the_host() -> None:
    port = closed_port()

    with pytest.raises(MetricsUnreachableError) as raised:
        urlopen_fetch(f"http://127.0.0.1:{port}/api/v1/query?query=up", timeout=2.0)

    message = str(raised.value)
    assert "/api/v1/query" in message
    assert "127.0.0.1" not in message
    assert str(port) not in message


def test_http_error_status_raises_a_metrics_error_naming_the_path_not_the_host(
    loopback: LoopbackServer,
) -> None:
    loopback.reply = (500, b"synthetic server failure")

    with pytest.raises((MetricsQueryError, MetricsUnreachableError)) as raised:
        urlopen_fetch(f"{loopback.base}/api/v1/query?query=up")

    message = str(raised.value)
    assert "/api/v1/query" in message
    assert "127.0.0.1" not in message


def test_non_http_scheme_is_refused_without_reading(tmp_path: Path) -> None:
    local_file = tmp_path / "not-a-metrics-server.json"
    local_file.write_text('{"status":"success","data":[]}', encoding="utf-8")

    with pytest.raises((ValueError, MetricsUnreachableError)):
        urlopen_fetch(local_file.as_uri())
