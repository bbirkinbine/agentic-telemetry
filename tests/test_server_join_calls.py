"""What the join asks VictoriaMetrics, and its warnings (spec 0005, Known models; D3).

The plan's join: known model IDs are read once per invocation, from the earliest
run start minus one scrape to `now`; a window-less run is `no_data` with a
warning; a run ending close to `now` gets a rerun warning.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from server_join_helpers import (
    BASE_URL,
    KNOWN,
    NOW,
    RUN_END,
    RUN_START,
    SERVER_VALUE_COLUMNS,
    model_readings,
    server_row,
)
from vm_stub import FakeVictoria

from agentic_telemetry.run_table.row import RunRow
from agentic_telemetry.server_metrics.http_fetch import MetricsUnreachableError
from agentic_telemetry.server_metrics.join import join_server_metrics


def three_rows() -> list[RunRow]:
    return [
        server_row("run-a"),  # starts 10:00:00
        server_row("run-b", ts="2026-10-22T09:30:00+00:00", wall_seconds=600),  # starts 09:20
        server_row("run-c", ts="2026-10-22T11:00:00+00:00", wall_seconds=60),
    ]


def test_known_model_ids_are_read_once_per_invocation() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())

    join_server_metrics(three_rows(), BASE_URL, fake, NOW)

    assert len(fake.label_calls()) == 1


def test_known_model_ids_cover_the_earliest_run_start_minus_a_scrape_up_to_now() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())

    join_server_metrics(three_rows(), BASE_URL, fake, NOW)

    (call,) = fake.label_calls()
    earliest_start = RUN_START - timedelta(minutes=40)  # run-b: 09:30:00 - 600 s
    assert float(call.param("start")) == (earliest_start - timedelta(seconds=30)).timestamp()
    assert float(call.param("end")) == NOW.timestamp()


def test_each_run_with_a_window_sends_eight_queries() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())

    join_server_metrics(three_rows(), BASE_URL, fake, NOW)

    assert len(fake.query_calls()) == 3 * 8


def test_every_request_goes_to_the_given_base_url() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())

    join_server_metrics(three_rows(), BASE_URL, fake, NOW)

    assert fake.urls
    assert all(url.startswith(f"{BASE_URL}/api/v1/") for url in fake.urls)


def test_rows_come_back_in_order_one_per_input_row() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())

    rows, _ = join_server_metrics(three_rows(), BASE_URL, fake, NOW)

    assert [row.run_id for row in rows] == ["run-a", "run-b", "run-c"]


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"ts": None}, id="no-ts"),
        pytest.param({"wall_seconds": None, "agent_seconds": None}, id="no-duration"),
    ],
)
def test_run_without_a_window_is_no_data_with_a_warning(overrides: dict[str, object]) -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())
    rows = [server_row("run-windowed"), server_row("run-windowless", **overrides)]

    joined, report = join_server_metrics(rows, BASE_URL, fake, NOW)

    assert [row.server_status for row in joined] == ["joined", "no_data"]
    assert all(getattr(joined[1], name) is None for name in SERVER_VALUE_COLUMNS)
    warnings = [w for w in report.warnings if "run-windowless" in w]
    assert len(warnings) == 1, report.warnings
    assert "window" in warnings[0].lower()
    assert not any("run-windowed" in w for w in report.warnings)


def test_run_ending_within_ninety_seconds_of_now_warns_to_rerun_later() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())
    now = RUN_END + timedelta(seconds=80)  # widened end is later than now - 60 s

    _, report = join_server_metrics([server_row("run-fresh")], BASE_URL, fake, now)

    fresh = [w for w in report.warnings if "run-fresh" in w]
    assert len(fresh) == 1, report.warnings
    assert "rerun" in fresh[0].lower()
    assert "latencyoffset" in fresh[0].lower()


def test_run_ending_well_before_now_gets_no_rerun_warning() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())
    now = RUN_END + timedelta(seconds=100)

    _, report = join_server_metrics([server_row("run-settled")], BASE_URL, fake, now)

    assert list(report.warnings) == []


def test_unreachable_metrics_url_raises_before_any_query() -> None:
    fake = FakeVictoria(
        known=KNOWN, raise_on_label=MetricsUnreachableError("/api/v1/label/model_id/values")
    )

    with pytest.raises(MetricsUnreachableError):
        join_server_metrics(three_rows(), BASE_URL, fake, NOW)

    assert fake.query_calls() == []


def test_failed_known_models_call_raises_unreachable() -> None:
    fake = FakeVictoria(known=KNOWN, label_error=True)

    with pytest.raises(MetricsUnreachableError):
        join_server_metrics(three_rows(), BASE_URL, fake, NOW)

    assert fake.query_calls() == []
