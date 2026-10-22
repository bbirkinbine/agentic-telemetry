"""The 8 instant queries read for one run window (spec 0005; D5 `increase_pure`, D8).

This file is the one place the MetricsQL strings are pinned. W is the window
duration + 30 s rounded up, evaluated at E = end + 30 s, so every lookback starts at
the run's own start: `increase_pure` already takes the last sample before its
window as the base (D8 removed a start-side widening that double-counted). `up` at
the start reads the unwidened start; `up` later reads the same [W] range at E, so a
load that finished just after the run ended still counts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from server_join_helpers import BASE_URL, RUN_END, RUN_START, model_readings
from vm_stub import FakeVictoria

from agentic_telemetry.server_metrics.readings import read_window
from agentic_telemetry.server_metrics.window import RunWindow

SELECTOR = '{service_name="inference-models"}'


def expected_queries(w: int) -> dict[str, str]:
    """Return the 8 query strings for lookback window `w` seconds."""
    counters = {
        name: f"sum by (model_id) (increase_pure(llamacpp_{name}{SELECTOR}[{w}s]))"
        for name in (
            "prompt_tokens_total",
            "tokens_predicted_total",
            "prompt_seconds_total",
            "tokens_predicted_seconds_total",
        )
    }
    return {
        **counters,
        "deferred": (
            f"max by (model_id) (max_over_time(llamacpp_requests_deferred{SELECTOR}[{w}s]))"
        ),
        "ctx": (f"max by (model_id) (last_over_time(llamacpp_n_tokens_max_total{SELECTOR}[{w}s]))"),
        "up_at_start": f"max by (model_id) (last_over_time(up{SELECTOR}[60s]))",
        "up_later": f"max by (model_id) (max_over_time(up{SELECTOR}[{w}s]))",
    }


def queried(window: RunWindow) -> list[tuple[str, float]]:
    """Run `read_window` against a stub and return its (query, time) pairs."""
    fake = FakeVictoria(readings=model_readings())
    read_window(BASE_URL, fake, window)
    return [(call.query, call.time) for call in fake.query_calls()]


def test_one_window_sends_exactly_the_eight_queries_at_their_times() -> None:
    window = RunWindow(start=RUN_START, end=RUN_END)  # 100 s
    widened_end = (RUN_END + timedelta(seconds=30)).timestamp()
    queries = expected_queries(w=130)

    pairs = queried(window)

    expected = [
        (queries["prompt_tokens_total"], widened_end),
        (queries["tokens_predicted_total"], widened_end),
        (queries["prompt_seconds_total"], widened_end),
        (queries["tokens_predicted_seconds_total"], widened_end),
        (queries["deferred"], widened_end),
        (queries["ctx"], widened_end),
        (queries["up_at_start"], RUN_START.timestamp()),
        (queries["up_later"], widened_end),
    ]
    assert sorted(pairs) == sorted(expected)


def test_counter_queries_use_increase_pure_never_plain_increase() -> None:
    pairs = queried(RunWindow(start=RUN_START, end=RUN_END))

    counter_queries = [query for query, _ in pairs if "_total" in query and "sum by" in query]
    assert len(counter_queries) == 4
    assert all("increase_pure(" in query for query in counter_queries)
    assert not any(" (increase(" in query for query, _ in pairs)


def test_every_selector_carries_the_inference_agent_service_name() -> None:
    pairs = queried(RunWindow(start=RUN_START, end=RUN_END))

    assert len(pairs) == 8
    assert all(SELECTOR in query for query, _ in pairs)


def test_fractional_window_rounds_the_counter_range_up_to_whole_seconds() -> None:
    end = RUN_START + timedelta(seconds=100.2)

    pairs = queried(RunWindow(start=RUN_START, end=end))

    ranged = [query for query, _ in pairs if "llamacpp_" in query or "max_over_time(up" in query]
    assert len(ranged) == 7
    assert all(query.endswith("[131s]))") for query in ranged), ranged


def test_zero_length_window_still_asks_up_over_at_least_one_second() -> None:
    instant = datetime(2026, 10, 22, 10, 0, 0, tzinfo=UTC)

    pairs = queried(RunWindow(start=instant, end=instant))

    queries = expected_queries(w=30)
    assert (queries["up_later"], instant.timestamp() + 30) in pairs
    assert (queries["prompt_tokens_total"], instant.timestamp() + 30) in pairs


def test_counter_lookback_starts_exactly_at_the_run_start() -> None:
    """D8: E - W is the run's start, so the base sample is the last scrape before it.

    A start-side widening would reach one more scrape back and count a preceding
    run's tokens as this run's.
    """
    pairs = queried(RunWindow(start=RUN_START, end=RUN_END))

    for query, at in pairs:
        if "increase_pure(" not in query:
            continue
        seconds = int(query.rsplit("[", 1)[1].split("s]", 1)[0])
        assert at - seconds == RUN_START.timestamp(), query
