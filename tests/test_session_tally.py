"""`SessionTally`: the per-session accumulator every client reader shares.

Seam chosen for the plan's `tally` module: `SessionTally()` takes no arguments;
readers feed it with `observe_timestamp(value)`, `count_assistant_turn(model)` and
`add_usage(tokens_in=..., tokens_cache_read=..., tokens_cache_write=...,
tokens_out=..., tokens_reasoning=...)` (all `object`, mistyped values skipped),
and read back `started_at`, `ended_at`, `span_seconds`, `models`,
`primary_model`, `assistant_turns` and the five token totals.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentic_telemetry.session_table.tally import SessionTally


def _instant(value: str | None) -> datetime:
    assert value is not None
    parsed = datetime.fromisoformat(value)
    assert parsed.utcoffset() == timedelta(0), f"not UTC: {value}"
    return parsed


def test_start_and_end_are_the_earliest_and_latest_timestamps_not_file_order() -> None:
    tally = SessionTally()
    for value in ("2026-10-20T10:05:00.000Z", "2026-10-20T10:00:00.000Z", "2026-10-20T10:02:00Z"):
        tally.observe_timestamp(value)

    assert _instant(tally.started_at) == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert _instant(tally.ended_at) == datetime(2026, 10, 20, 10, 5, tzinfo=UTC)


def test_offset_timestamps_are_normalized_to_utc() -> None:
    tally = SessionTally()
    tally.observe_timestamp("2026-10-20T12:00:00+02:00")

    assert _instant(tally.started_at) == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def test_naive_timestamp_is_taken_as_utc() -> None:
    tally = SessionTally()
    tally.observe_timestamp("2026-10-20T10:00:00")

    assert _instant(tally.started_at) == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def test_span_is_the_wall_seconds_between_first_and_last_timestamp() -> None:
    tally = SessionTally()
    tally.observe_timestamp("2026-10-20T10:00:00.000Z")
    tally.observe_timestamp("2026-10-20T10:01:30.500Z")

    assert tally.span_seconds == pytest.approx(90.5)
    assert isinstance(tally.span_seconds, float)


@pytest.mark.parametrize(
    "junk",
    [None, 1760954400, "not-a-time", "", ["2026-10-20"], "0001-01-01T00:00:00+05:00"],
)
def test_unusable_timestamps_are_skipped(junk: object) -> None:
    tally = SessionTally()
    tally.observe_timestamp(junk)
    tally.observe_timestamp("2026-10-20T10:00:00Z")

    assert _instant(tally.started_at) == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert tally.span_seconds == 0.0


def test_no_timestamps_leave_time_columns_null() -> None:
    tally = SessionTally()

    assert tally.started_at is None
    assert tally.ended_at is None
    assert tally.span_seconds is None


def test_models_are_listed_once_each_in_first_seen_order() -> None:
    tally = SessionTally()
    for model in ("model-b", "model-a", "model-b", "model-c", "model-a"):
        tally.count_assistant_turn(model)

    assert tally.models == ("model-b", "model-a", "model-c")


def test_primary_model_has_the_most_assistant_turns() -> None:
    tally = SessionTally()
    for model in ("model-b", "model-a", "model-a"):
        tally.count_assistant_turn(model)

    assert tally.primary_model == "model-a"


def test_primary_model_tie_goes_to_the_first_seen() -> None:
    tally = SessionTally()
    for model in ("model-b", "model-a", "model-a", "model-b"):
        tally.count_assistant_turn(model)

    assert tally.primary_model == "model-b"


def test_turn_without_a_usable_model_counts_as_a_turn_but_not_a_model() -> None:
    tally = SessionTally()
    tally.count_assistant_turn("model-a")
    tally.count_assistant_turn(None)
    tally.count_assistant_turn(42)

    assert tally.assistant_turns == 3
    assert tally.models == ("model-a",)
    assert tally.primary_model == "model-a"


def test_no_models_leave_primary_model_null() -> None:
    tally = SessionTally()
    tally.count_assistant_turn(None)

    assert tally.models == ()
    assert tally.primary_model is None


def test_usage_sums_per_column_across_calls() -> None:
    tally = SessionTally()
    tally.add_usage(tokens_in=100, tokens_cache_read=10, tokens_out=20, tokens_cache_write=1)
    tally.add_usage(tokens_in=50, tokens_cache_read=5, tokens_out=7, tokens_reasoning=3)

    assert tally.tokens_in == 150
    assert tally.tokens_cache_read == 15
    assert tally.tokens_cache_write == 1
    assert tally.tokens_out == 27
    assert tally.tokens_reasoning == 3


def test_a_column_never_recorded_stays_null_while_a_recorded_zero_is_zero() -> None:
    tally = SessionTally()
    tally.add_usage(tokens_in=0, tokens_out=12)

    assert tally.tokens_in == 0
    assert tally.tokens_out == 12
    assert tally.tokens_cache_read is None
    assert tally.tokens_cache_write is None
    assert tally.tokens_reasoning is None


@pytest.mark.parametrize("junk", ["12", 1.5, True, None, [3], {"n": 3}, 10**20])
def test_mistyped_usage_values_are_skipped_not_raised(junk: object) -> None:
    tally = SessionTally()
    tally.add_usage(tokens_in=junk, tokens_out=5)
    tally.add_usage(tokens_in=4)

    assert tally.tokens_in == 4
    assert tally.tokens_out == 5


def test_a_sum_that_leaves_int64_makes_the_column_null_not_an_undercount() -> None:
    tally = SessionTally()
    for _ in range(3):
        tally.add_usage(tokens_in=2**62, tokens_out=1)

    assert tally.tokens_in is None
    assert tally.tokens_out == 3


def test_served_models_are_tracked_apart_from_requested_models() -> None:
    tally = SessionTally()
    tally.count_assistant_turn("local-fast", served_model="weights-q4")
    tally.count_assistant_turn("local-alias", served_model="weights-q4")
    tally.count_assistant_turn("local-alias", served_model="weights-q8")
    tally.count_assistant_turn("local-alias")

    assert tally.models == ("local-fast", "local-alias")
    assert tally.primary_model == "local-alias"
    assert tally.served_models == ("weights-q4", "weights-q8")
    assert tally.primary_served_model == "weights-q4"
