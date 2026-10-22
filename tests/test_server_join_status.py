"""`server_status` for every case the join can reach (spec 0005, Status; D3 overlap)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from server_join_helpers import (
    ALIAS_MAYA,
    ALIAS_UNKNOWN,
    BASE_URL,
    FLASH,
    GLM,
    KNOWN,
    MAYA,
    NOW,
    QWEN,
    RUN_END,
    RUN_START,
    SERVER_VALUE_COLUMNS,
    merge_readings,
    model_readings,
    server_row,
)
from vm_stub import FakeVictoria

from agentic_telemetry.server_metrics.http_fetch import MetricsQueryError
from agentic_telemetry.server_metrics.join import join_server_metrics
from agentic_telemetry.server_metrics.window import RunWindow, widen

DEFAULT_SPAN = (RUN_START - timedelta(minutes=5), RUN_END + timedelta(minutes=5))


def join_one(fake: FakeVictoria, **overrides: object) -> dict[str, object]:
    """Join one default row (with overrides) and return its server columns by name."""
    (row,), _ = join_server_metrics([server_row(**overrides)], BASE_URL, fake, NOW)
    names = [field.name for field in dataclasses.fields(row) if field.name.startswith("server_")]
    return {name: getattr(row, name) for name in names}


def test_exact_tokens_on_a_served_model_are_joined() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())

    columns = join_one(fake)

    assert columns["server_status"] == "joined"
    assert columns["server_model_id"] == QWEN
    assert columns["server_model_source"] == "served"
    assert [columns["server_token_match"]] == ["exact"]


def test_no_transcript_tokens_with_series_is_joined_not_compared() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())

    columns = join_one(
        fake,
        requested_models=None,
        served_models=None,
        model_id=QWEN,
        tokens_in=None,
        tokens_out=None,
    )

    assert columns["server_status"] == "joined"
    assert columns["server_model_source"] == "requested"
    assert [columns["server_token_match"]] == ["not_compared"]


def test_server_saw_fewer_tokens_is_joined_approx() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings(prompt=900.0))

    columns = join_one(fake)

    assert columns["server_status"] == "joined_approx"
    assert [columns["server_token_match"]] == ["server_less"]
    assert columns["server_prompt_tokens"] == 900


def test_server_saw_more_tokens_is_overlap_with_columns_kept() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings(prompt=1500.0))

    columns = join_one(fake)

    assert columns["server_status"] == "overlap"
    assert [columns["server_token_match"]] == ["server_more"]
    assert columns["server_prompt_tokens"] == 1500
    assert columns["server_decode_tps"] == 20.0


def test_mixed_token_result_counts_as_server_more_and_overlap() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings(prompt=1500.0, predicted=150.0))

    columns = join_one(fake)

    assert [columns["server_token_match"]] == ["server_more"]
    assert columns["server_status"] == "overlap"


def test_two_table_runs_on_one_model_with_overlapping_windows_are_both_overlap() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())
    first = server_row("run-first")  # [10:00:00, 10:01:40]
    second = server_row("run-second", ts="2026-10-22T10:01:30+00:00", wall_seconds=60)

    rows, _ = join_server_metrics([first, second], BASE_URL, fake, NOW)

    assert [row.server_token_match for row in rows] == ["exact", "exact"]
    assert [row.server_status for row in rows] == ["overlap", "overlap"]
    assert all(row.server_prompt_tokens == 1000 for row in rows), "columns stay filled"


def test_back_to_back_runs_compare_unwidened_windows_and_stay_joined() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())
    first = server_row("run-first")  # [10:00:00, 10:01:40]
    second = server_row("run-second", ts="2026-10-22T10:03:20+00:00", wall_seconds=100)
    first_widened = widen(RunWindow(RUN_START, RUN_END), 30)
    assert first_widened.end > RUN_END, "the widened windows do overlap"

    rows, _ = join_server_metrics([first, second], BASE_URL, fake, NOW)

    assert [row.server_status for row in rows] == ["joined", "joined"]


def test_overlapping_runs_on_different_models_stay_joined() -> None:
    readings = merge_readings(model_readings(QWEN), model_readings(FLASH))
    fake = FakeVictoria(known=KNOWN, readings=readings)
    first = server_row("run-qwen")
    second = server_row("run-flash", served_models=(FLASH,), requested_models=(FLASH,))

    rows, _ = join_server_metrics([first, second], BASE_URL, fake, NOW)

    assert [row.server_model_id for row in rows] == [QWEN, FLASH]
    assert [row.server_status for row in rows] == ["joined", "joined"]


def test_unknown_names_with_nothing_inferred_are_uncovered() -> None:
    fake = FakeVictoria(known=KNOWN, readings={})

    columns = join_one(
        fake,
        requested_models=(ALIAS_UNKNOWN,),
        served_models=(),
        model_id=ALIAS_UNKNOWN,
    )

    assert columns["server_status"] == "uncovered"
    assert columns["server_model_id"] is None
    assert columns["server_model_source"] == "none"
    assert [columns["server_token_match"]] == ["not_compared"]
    assert all(columns[name] is None for name in SERVER_VALUE_COLUMNS)


def test_maya_build_without_metrics_is_uncovered() -> None:
    fake = FakeVictoria(known=(QWEN, FLASH), readings={})

    columns = join_one(
        fake, requested_models=(ALIAS_MAYA,), served_models=(GLM,), model_id=ALIAS_MAYA
    )

    assert columns["server_status"] == "uncovered"
    assert columns["server_model_source"] == "none"


def test_maya_served_name_never_wins_over_a_different_known_requested_id() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings(QWEN))

    columns = join_one(fake, requested_models=(MAYA,), served_models=(GLM,), model_id=ALIAS_MAYA)

    assert columns["server_model_id"] == MAYA
    assert columns["server_model_source"] == "requested"
    assert columns["server_status"] == "no_data", "Maya has no series in the window"


def test_run_older_than_the_agent_is_no_data_beside_a_joined_run() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings(), active_between=DEFAULT_SPAN)
    recent = server_row("run-recent")
    old = server_row(
        "run-old", ts="2026-09-01T08:00:00+00:00", requested_models=(QWEN,), served_models=()
    )

    rows, _ = join_server_metrics([recent, old], BASE_URL, fake, NOW)

    assert [row.server_status for row in rows] == ["joined", "no_data"]
    old_row = rows[1]
    assert old_row.server_model_id == QWEN
    assert [old_row.server_token_match] == ["not_compared"]
    assert all(getattr(old_row, name) is None for name in SERVER_VALUE_COLUMNS)


def test_failed_query_is_error_for_that_run_only_with_a_warning_naming_run_and_path() -> None:
    later_end = datetime(2026, 10, 22, 11, 1, 40, tzinfo=UTC)
    fake = FakeVictoria(
        known=KNOWN,
        readings=model_readings(),
        query_error_between=(later_end - timedelta(minutes=5), later_end + timedelta(minutes=5)),
    )
    good = server_row("run-good")
    failing = server_row("run-failing", ts="2026-10-22T11:01:40+00:00")

    rows, report = join_server_metrics([good, failing], BASE_URL, fake, NOW)

    assert [row.server_status for row in rows] == ["joined", "error"]
    assert all(getattr(rows[1], name) is None for name in SERVER_VALUE_COLUMNS)
    warnings = list(report.warnings)
    assert any("run run-failing: query failed at /api/v1/query" in w for w in warnings), warnings
    assert not any("telemetry.example" in w for w in warnings), "the host is never printed"
    assert not any("run-good" in w for w in warnings)


def test_fetch_raising_a_query_error_is_error_status() -> None:
    fake = FakeVictoria(known=KNOWN, raise_on_query=MetricsQueryError("/api/v1/query", "HTTP 422"))

    (row,), report = join_server_metrics([server_row("run-422")], BASE_URL, fake, NOW)

    assert row.server_status == "error"
    assert any("run run-422: query failed at /api/v1/query" in w for w in report.warnings)
