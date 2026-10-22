"""A stub VictoriaMetrics for server-metrics tests: a `Fetch` callable over canned JSON.

The response shapes copy the Prometheus HTTP API documentation, an independent
source: https://prometheus.io/docs/prometheus/latest/querying/api/ (Apache-2.0,
retrieved 2026-10-22).
- Envelope: `{"status": "success" | "error", "data": ..., "errorType": ..., "error": ...}`;
  `errorType` and `error` appear only when status is "error".
- Instant vector items: `{"metric": {...}, "value": [<unix_time>, "<sample_value>"]}`.
- Label values: `{"status": "success", "data": ["200", "504"]}`.

Every model value here is synthetic; the host is `telemetry.example` (RFC 2606).
Nothing in this module imports the code under test.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

LABEL_VALUES_PATH = "/api/v1/label/model_id/values"
QUERY_PATH = "/api/v1/query"

# Reading keys: the `llamacpp_*` metric name a query selects, or `up_at_start`
# (`last_over_time(up{...})`) and `up_later` (`max_over_time(up{...})`).
PROMPT = "llamacpp_prompt_tokens_total"
PREDICTED = "llamacpp_tokens_predicted_total"
PROMPT_SECONDS = "llamacpp_prompt_seconds_total"
PREDICTED_SECONDS = "llamacpp_tokens_predicted_seconds_total"
DEFERRED = "llamacpp_requests_deferred"
CTX = "llamacpp_n_tokens_max_total"
UP_AT_START = "up_at_start"
UP_LATER = "up_later"

Readings = Mapping[str, Mapping[str, float | str]]

_SELECTOR = re.compile(r"\b(llamacpp_[a-z_]+|up)\{")


def vector_response(values: Mapping[str, float | str], at: float = 0.0) -> bytes:
    """Return a success envelope holding an instant vector, one item per model ID.

    A str value is sent as is (for "NaN", "+Inf"); a number is sent as its text.
    """
    result = [
        {
            "metric": {"model_id": model_id},
            "value": [at, value if isinstance(value, str) else repr(float(value))],
        }
        for model_id, value in values.items()
    ]
    payload = {"status": "success", "data": {"resultType": "vector", "result": result}}
    return json.dumps(payload).encode("utf-8")


def label_values_response(values: Iterable[str]) -> bytes:
    """Return a success envelope holding label values."""
    return json.dumps({"status": "success", "data": list(values)}).encode("utf-8")


def error_response(error_type: str = "bad_data", error: str = "synthetic failure") -> bytes:
    """Return an error envelope (no `data`)."""
    payload = {"status": "error", "errorType": error_type, "error": error}
    return json.dumps(payload).encode("utf-8")


def reading_key(query: str) -> str:
    """Return the reading key a MetricsQL query asks for (see the constants above)."""
    match = _SELECTOR.search(query)
    if match is None:
        raise AssertionError(f"stub cannot tell which metric this query selects: {query!r}")
    name = match.group(1)
    if name != "up":
        return name
    return UP_AT_START if "last_over_time(up{" in query else UP_LATER


@dataclass(frozen=True)
class Call:
    """One URL the stub was asked for, split into path and decoded parameters."""

    url: str
    path: str
    params: dict[str, list[str]]

    def param(self, name: str) -> str:
        """Return the single value of parameter `name`, asserting it was sent once."""
        values = self.params.get(name, [])
        assert len(values) == 1, f"expected one {name!r} parameter in {self.url}, got {values}"
        return values[0]

    @property
    def query(self) -> str:
        """The `query` parameter."""
        return self.param("query")

    @property
    def time(self) -> float:
        """The `time` parameter as unix seconds."""
        return float(self.param("time"))


class FakeVictoria:
    """A callable `Fetch` that records every URL and answers with canned JSON.

    Args:
        known: Model IDs the label-values endpoint returns.
        readings: Reading key -> {model_id: value}. A key left out is an empty vector.
        active_between: When set, readings are returned only for queries evaluated
            within this closed interval; any other evaluation time gets empty vectors.
        query_error_between: Queries evaluated within this closed interval get an
            error envelope.
        label_error: The label-values call gets an error envelope.
        raise_on_label: Raised by the label-values call.
        raise_on_query: Raised by every `/api/v1/query` call.
    """

    def __init__(
        self,
        *,
        known: Iterable[str] = (),
        readings: Readings | None = None,
        active_between: tuple[datetime, datetime] | None = None,
        query_error_between: tuple[datetime, datetime] | None = None,
        label_error: bool = False,
        raise_on_label: BaseException | None = None,
        raise_on_query: BaseException | None = None,
    ) -> None:
        self.known = list(known)
        self.readings: Readings = readings or {}
        self.active_between = active_between
        self.query_error_between = query_error_between
        self.label_error = label_error
        self.raise_on_label = raise_on_label
        self.raise_on_query = raise_on_query
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        parts = urlsplit(url)
        if parts.path.endswith(LABEL_VALUES_PATH):
            if self.raise_on_label is not None:
                raise self.raise_on_label
            return error_response() if self.label_error else label_values_response(self.known)
        if parts.path.endswith(QUERY_PATH):
            if self.raise_on_query is not None:
                raise self.raise_on_query
            call = Call(url, parts.path, parse_qs(parts.query))
            if _within(call.time, self.query_error_between):
                return error_response()
            if self.active_between is not None and not _within(call.time, self.active_between):
                return vector_response({}, call.time)
            return vector_response(self.readings.get(reading_key(call.query), {}), call.time)
        raise AssertionError(f"stub got an unexpected path {parts.path!r} in {url}")

    @property
    def calls(self) -> list[Call]:
        """Every URL asked for, in order."""
        return [Call(url, urlsplit(url).path, parse_qs(urlsplit(url).query)) for url in self.urls]

    def label_calls(self) -> list[Call]:
        """The label-values calls, in order."""
        return [call for call in self.calls if call.path.endswith(LABEL_VALUES_PATH)]

    def query_calls(self) -> list[Call]:
        """The instant-query calls, in order."""
        return [call for call in self.calls if call.path.endswith(QUERY_PATH)]


def _within(at: float, interval: tuple[datetime, datetime] | None) -> bool:
    """True when unix time `at` lies in the closed `interval`."""
    if interval is None:
        return False
    return interval[0].timestamp() <= at <= interval[1].timestamp()
