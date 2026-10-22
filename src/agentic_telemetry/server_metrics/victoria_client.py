"""Read model IDs and per-model instant vectors from VictoriaMetrics (spec 0005).

Requests follow "API examples", VictoriaMetrics (Apache-2.0),
https://docs.victoriametrics.com/victoriametrics/url-examples/ , retrieved
2026-10-22: `/api/v1/label/<name>/values` answers for the last day from 00:00
UTC unless `start` and `end` widen it, and `/api/v1/query` answers one instant
vector per call. `query_range` is deliberately unused: every reading here is a
single value per model over a lookback window, which one instant query per
metric gives. `nocache=1` is sent on each query so a rerun right after a run
re-reads rather than replaying a cached empty answer (spec 0005, External
references).

Response bodies are those documented by Prometheus' HTTP API
(Apache-2.0, https://prometheus.io/docs/prometheus/latest/querying/api/).
"""

import json
import math
from datetime import datetime
from urllib.parse import urlencode

from agentic_telemetry.run_table.json_values import JsonObject, as_object, object_list, opt_str
from agentic_telemetry.server_metrics.http_fetch import Fetch, MetricsQueryError
from agentic_telemetry.server_metrics.window import SERVICE_NAME

LABEL_VALUES_PATH = "/api/v1/label/model_id/values"
QUERY_PATH = "/api/v1/query"

# Only models the inference host's agent scrapes are known; this keeps out the
# temporary 0003 capture series, which carry a `model_id` too.
_MODEL_ID_MATCH = f'up{{service_name="{SERVICE_NAME}"}}'


def known_model_ids(
    base_url: str, fetch: Fetch, *, start: datetime, end: datetime, match: str = _MODEL_ID_MATCH
) -> frozenset[str]:
    """Return every `model_id` VictoriaMetrics has seen between `start` and `end`.

    Args:
        base_url: The VictoriaMetrics base URL, without a trailing slash.
        fetch: The HTTP seam.
        start: How far back to look; pass the earliest run in the table so runs
            older than the agent still name a model they have no data for.
        end: Usually now.
        match: A series selector limiting which series' label values count.

    Returns:
        The distinct model IDs.

    Raises:
        MetricsQueryError: The request failed or its body was unusable.
    """
    params = {"match[]": match, "start": _unix(start), "end": _unix(end)}
    data = _data(fetch, LABEL_VALUES_PATH, f"{base_url}{LABEL_VALUES_PATH}?{urlencode(params)}")
    if not isinstance(data, list):
        raise MetricsQueryError(LABEL_VALUES_PATH, "label values were not a JSON array")
    return frozenset(value for value in (opt_str(item) for item in data) if value)


def instant_by_model(base_url: str, fetch: Fetch, query: str, at: datetime) -> dict[str, float]:
    """Return one value per `model_id` for `query` evaluated at `at`.

    Args:
        base_url: The VictoriaMetrics base URL, without a trailing slash.
        fetch: The HTTP seam.
        query: MetricsQL for an instant vector grouped by `model_id`.
        at: The evaluation time.

    Returns:
        Model ID to sample value. A series whose `model_id` label is missing, and
        one whose value is not a finite number (NaN, +/-Inf), are left out: both
        mean "this query says nothing about that model".

    Raises:
        MetricsQueryError: The request failed or its body was unusable.
    """
    params = {"query": query, "time": _unix(at), "nocache": "1"}
    url = f"{base_url}{QUERY_PATH}?{urlencode(params)}"
    data = _data(fetch, QUERY_PATH, url)
    vector = as_object(data)
    if vector is None:
        raise MetricsQueryError(QUERY_PATH, "query data was not a JSON object")
    if opt_str(vector.get("resultType")) != "vector":
        raise MetricsQueryError(QUERY_PATH, "resultType was not vector")
    result = vector.get("result")
    if not isinstance(result, list):
        raise MetricsQueryError(QUERY_PATH, "query result was not a JSON array")
    values: dict[str, float] = {}
    for item in object_list(result):
        value = _sample_value(item)
        metric = as_object(item.get("metric")) or {}
        model_id = opt_str(metric.get("model_id"))
        if model_id is not None and math.isfinite(value):
            values[model_id] = value
    return values


def _data(fetch: Fetch, path: str, url: str) -> object:
    """Fetch `url` and return its `data`, raising for an error status or body.

    Args:
        fetch: The HTTP seam.
        path: The API path, the only part of the URL named in an error.
        url: The full URL to fetch.

    Returns:
        The decoded `data` value, unchecked.

    Raises:
        MetricsQueryError: The body was not JSON, its status was not "success",
            or it carried no status at all.
    """
    body = fetch(url)
    try:
        decoded: object = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as error:  # json.JSONDecodeError, a subclass of ValueError
        raise MetricsQueryError(path, "response was not JSON") from error
    envelope = as_object(decoded)
    if envelope is None:
        raise MetricsQueryError(path, "response was not a JSON object")
    status = opt_str(envelope.get("status"))
    if status != "success":
        raise MetricsQueryError(path, _failure_detail(envelope, status))
    return envelope.get("data")


def _failure_detail(envelope: JsonObject, status: str | None) -> str:
    """Describe an error envelope as its type and message."""
    detail = opt_str(envelope.get("error")) or opt_str(envelope.get("errorType")) or "no detail"
    return f"status {status or 'missing'}: {detail}"


def _sample_value(item: JsonObject) -> float:
    """Return a vector item's sample value.

    Raises:
        MetricsQueryError: The item's `value` is not the documented
            `[unix_seconds, "<number>"]` pair.
    """
    pair = item.get("value")
    if not isinstance(pair, list) or len(pair) != 2:
        raise MetricsQueryError(QUERY_PATH, "sample was not a [time, value] pair")
    text = opt_str(pair[1])
    if text is None:
        raise MetricsQueryError(QUERY_PATH, "sample value was not a string")
    try:
        return float(text)
    except ValueError as error:
        raise MetricsQueryError(QUERY_PATH, f"sample value was not a number: {text!r}") from error


def _unix(at: datetime) -> str:
    """Render an aware datetime as the unix seconds both APIs accept."""
    return str(at.timestamp())
