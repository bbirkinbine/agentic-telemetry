"""The VictoriaMetrics client: request URLs and response parsing (spec 0005).

Request shapes follow the plan (VictoriaMetrics HTTP API, Apache-2.0, retrieved
2026-10-22); responses come from `vm_stub`, which copies Prometheus' documented shape.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from server_join_helpers import BASE_URL, FLASH, QWEN
from vm_stub import (
    Call,
    error_response,
    label_values_response,
    vector_response,
)

from agentic_telemetry.server_metrics.http_fetch import MetricsQueryError
from agentic_telemetry.server_metrics.victoria_client import (
    LABEL_VALUES_PATH,
    QUERY_PATH,
    instant_by_model,
    known_model_ids,
)

UP_MATCH = 'up{service_name="inference-models"}'
PROMPT_QUERY = (
    "sum by (model_id) "
    '(increase_pure(llamacpp_prompt_tokens_total{service_name="inference-models"}[160s]))'
)
AT = datetime(2026, 10, 22, 10, 2, 10, tzinfo=UTC)


class Recorder:
    """A `Fetch` that answers every URL with one body and records the URLs."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[Call] = []

    def __call__(self, url: str) -> bytes:
        parts = urlsplit(url)
        self.calls.append(Call(url, parts.path, parse_qs(parts.query)))
        return self.body


def test_paths_are_the_victoria_metrics_api_paths() -> None:
    assert LABEL_VALUES_PATH == "/api/v1/label/model_id/values"
    assert QUERY_PATH == "/api/v1/query"


def test_known_model_ids_returns_the_label_values_as_a_set() -> None:
    fetch = Recorder(label_values_response([QWEN, FLASH, QWEN]))
    start = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)

    known = known_model_ids(BASE_URL, fetch, start=start, end=AT)

    assert known == frozenset({QWEN, FLASH})


def test_known_model_ids_sends_match_start_and_end_in_unix_seconds() -> None:
    fetch = Recorder(label_values_response([QWEN]))
    start = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)

    known_model_ids(BASE_URL, fetch, start=start, end=AT)

    (call,) = fetch.calls
    assert call.url.startswith(f"{BASE_URL}{LABEL_VALUES_PATH}?")
    assert call.param("match[]") == UP_MATCH
    assert float(call.param("start")) == start.timestamp()
    assert float(call.param("end")) == AT.timestamp()


def test_known_model_ids_error_status_raises_naming_the_path() -> None:
    fetch = Recorder(error_response())

    with pytest.raises(MetricsQueryError) as raised:
        known_model_ids(BASE_URL, fetch, start=AT, end=AT)

    assert LABEL_VALUES_PATH in str(raised.value)
    assert "telemetry.example" not in str(raised.value)


def test_instant_query_sends_query_time_and_nocache() -> None:
    fetch = Recorder(vector_response({QWEN: 1000.0}))

    instant_by_model(BASE_URL, fetch, PROMPT_QUERY, AT)

    (call,) = fetch.calls
    assert call.url.startswith(f"{BASE_URL}{QUERY_PATH}?")
    assert call.query == PROMPT_QUERY
    assert call.time == AT.timestamp()
    assert call.param("nocache") == "1"


def test_instant_query_returns_one_value_per_model_id() -> None:
    fetch = Recorder(vector_response({QWEN: 17784.0, FLASH: 9298.0}))

    values = instant_by_model(BASE_URL, fetch, PROMPT_QUERY, AT)

    assert values == {QWEN: 17784.0, FLASH: 9298.0}


def test_empty_vector_gives_an_empty_mapping() -> None:
    fetch = Recorder(vector_response({}))

    assert instant_by_model(BASE_URL, fetch, PROMPT_QUERY, AT) == {}


@pytest.mark.parametrize("not_a_number", ["NaN", "+Inf", "-Inf"])
def test_non_finite_sample_means_the_model_is_absent(not_a_number: str) -> None:
    fetch = Recorder(vector_response({QWEN: not_a_number, FLASH: "5"}))

    values = instant_by_model(BASE_URL, fetch, PROMPT_QUERY, AT)

    assert values == {FLASH: 5.0}


def _shaped(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(error_response("execution", "synthetic"), id="status-error"),
        pytest.param(b"<html>not json</html>", id="not-json"),
        pytest.param(_shaped({"status": "success", "data": []}), id="data-not-an-object"),
        pytest.param(
            _shaped({"status": "success", "data": {"resultType": "vector", "result": "x"}}),
            id="result-not-a-list",
        ),
        pytest.param(
            _shaped(
                {
                    "status": "success",
                    "data": {
                        "resultType": "vector",
                        "result": [{"metric": {"model_id": QWEN}, "value": ["1000"]}],
                    },
                }
            ),
            id="value-not-a-pair",
        ),
        pytest.param(
            _shaped(
                {
                    "status": "success",
                    "data": {
                        "resultType": "vector",
                        "result": [{"metric": {"model_id": QWEN}, "value": [1, "many"]}],
                    },
                }
            ),
            id="value-not-a-number",
        ),
    ],
)
def test_unusable_response_raises_query_error_naming_the_path_not_the_host(body: bytes) -> None:
    fetch = Recorder(body)

    with pytest.raises(MetricsQueryError) as raised:
        instant_by_model(BASE_URL, fetch, PROMPT_QUERY, AT)

    assert QUERY_PATH in str(raised.value)
    assert "telemetry.example" not in str(raised.value)
