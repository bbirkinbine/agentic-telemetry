"""The eight instant queries that read one run window from the inference host.

Each query is grouped by `model_id`, so one query serves every model at once and
also answers resolution rule 3 (which model's counters moved). Model IDs never
appear in a query string, so nothing here has to escape one.

`increase_pure` rather than `increase` (spec 0005, D5): llama.cpp counters start
at 0 when the server starts and their series exist only while the model is
loaded, so a cold-start run's series begins inside its own window, and
`increase` ignores a first value it considers too big. Source: "MetricsQL",
VictoriaMetrics (Apache-2.0), retrieved 2026-10-22,
https://docs.victoriametrics.com/victoriametrics/metricsql/

The per-scrape `*_seconds` throughput gauges are not queried: llama.cpp resets
their buckets on every `/metrics` call (spec 0007), so a rate is taken from the
counter increases instead.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from agentic_telemetry.server_metrics.http_fetch import Fetch
from agentic_telemetry.server_metrics.victoria_client import instant_by_model
from agentic_telemetry.server_metrics.window import SCRAPE_INTERVAL_SECONDS, SERVICE_NAME, RunWindow

SELECTOR = f'{{service_name="{SERVICE_NAME}"}}'

PROMPT_TOKENS = "llamacpp_prompt_tokens_total"
PREDICTED_TOKENS = "llamacpp_tokens_predicted_total"
PROMPT_SECONDS = "llamacpp_prompt_seconds_total"
PREDICTED_SECONDS = "llamacpp_tokens_predicted_seconds_total"
DEFERRED = "llamacpp_requests_deferred"
CTX_HIGH_WATER = "llamacpp_n_tokens_max_total"
UP = "up"

# The `up` lookback at a run's start: two scrapes, so a model that was up just
# before the run began still reads as up.
_UP_AT_START_LOOKBACK_SECONDS = 60

COUNTER_METRICS = (PROMPT_TOKENS, PREDICTED_TOKENS, PROMPT_SECONDS, PREDICTED_SECONDS)


@dataclass(frozen=True)
class WindowReadings:
    """Per-model values for one run window; a model with no series is simply absent.

    `prompt`, `predicted`, `prompt_seconds` and `predicted_seconds` are counter
    increases from the run's start to one scrape past its end; `deferred` and
    `ctx` are that span's maximum and last sample; `up_at_start` is the model's
    `up` at the run's start and `up_later` the largest `up` over the same span.
    """

    prompt: Mapping[str, float]
    predicted: Mapping[str, float]
    prompt_seconds: Mapping[str, float]
    predicted_seconds: Mapping[str, float]
    deferred: Mapping[str, float]
    ctx: Mapping[str, float]
    up_at_start: Mapping[str, float]
    up_later: Mapping[str, float]

    def moved_models(self) -> frozenset[str]:
        """Model IDs whose token counters rose at all: resolution rule 3."""
        return frozenset(
            model_id
            for vector in (self.prompt, self.predicted)
            for model_id, value in vector.items()
            if value > 0
        )


def read_window(base_url: str, fetch: Fetch, window: RunWindow) -> WindowReadings:
    """Read every column's raw value for `window`, one instant query per metric.

    Every ranged query looks back over the run plus one scrape interval,
    evaluated one scrape past the run's end, so the lookback starts at the run's
    own start (spec 0005, D8). The end-side scrape catches the run's last
    tokens. There is no start-side widening: `increase_pure` already takes the
    last sample before its window as the base, and reaching one scrape further
    back would count a preceding run's tokens as this run's. `up` at the start is
    read at the start itself; `up` later uses the same span, so a load that
    finished just after a run gave up still reads as a cold start.

    Args:
        base_url: The VictoriaMetrics base URL, without a trailing slash.
        fetch: The HTTP seam.
        window: The run's unwidened window.

    Returns:
        The readings for every model that answered.

    Raises:
        MetricsQueryError: A query failed or answered something unusable.
        MetricsUnreachableError: The endpoint could not be reached.
    """
    duration = (window.end - window.start).total_seconds()
    counter_range = _whole_seconds(duration + SCRAPE_INTERVAL_SECONDS)
    at_end = window.end + timedelta(seconds=SCRAPE_INTERVAL_SECONDS)
    increases = {
        metric: instant_by_model(base_url, fetch, _increase(metric, counter_range), at_end)
        for metric in COUNTER_METRICS
    }
    return WindowReadings(
        prompt=increases[PROMPT_TOKENS],
        predicted=increases[PREDICTED_TOKENS],
        prompt_seconds=increases[PROMPT_SECONDS],
        predicted_seconds=increases[PREDICTED_SECONDS],
        deferred=instant_by_model(base_url, fetch, _max_over(DEFERRED, counter_range), at_end),
        ctx=instant_by_model(base_url, fetch, _last_over(CTX_HIGH_WATER, counter_range), at_end),
        up_at_start=instant_by_model(
            base_url,
            fetch,
            _last_over(UP, _UP_AT_START_LOOKBACK_SECONDS),
            window.start,
        ),
        up_later=instant_by_model(base_url, fetch, _max_over(UP, counter_range), at_end),
    )


def _increase(metric: str, seconds: int) -> str:
    """Counter increase per model over `seconds`."""
    return f"sum by (model_id) (increase_pure({metric}{SELECTOR}[{seconds}s]))"


def _max_over(metric: str, seconds: int) -> str:
    """Largest raw sample per model over `seconds`."""
    return f"max by (model_id) (max_over_time({metric}{SELECTOR}[{seconds}s]))"


def _last_over(metric: str, seconds: int) -> str:
    """Last raw sample per model over `seconds`."""
    return f"max by (model_id) (last_over_time({metric}{SELECTOR}[{seconds}s]))"


def _whole_seconds(seconds: float) -> int:
    """Round a duration up to whole seconds, with one second as the floor."""
    return max(1, math.ceil(seconds))
