"""A run's time window for the server-metrics join (spec 0005, Window)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from collector_files import AGENT_CONFIG, load_yaml_mapping, relative

from agentic_telemetry.server_metrics.window import (
    SCRAPE_INTERVAL_SECONDS,
    SERVICE_NAME,
    RunWindow,
    overlaps,
    run_window,
    start_stamp,
    widen,
)


def at(hour: int, minute: int, second: int = 0) -> datetime:
    """An aware UTC time on 2026-10-22."""
    return datetime(2026, 10, 22, hour, minute, second, tzinfo=UTC)


def test_start_stamp_is_read_from_the_end_of_a_run_id() -> None:
    stamp = start_stamp("delegate-handoff-20261022T024448Z-12345")

    assert stamp == datetime(2026, 10, 22, 2, 44, 48, tzinfo=UTC)


@pytest.mark.parametrize(
    "run_id",
    [
        pytest.param("alpha-run-green", id="no-stamp"),
        pytest.param("delegate-handoff-20261022T024448Z", id="stamp-without-pid"),
        pytest.param("delegate-20261022T024448Z-12345-retry", id="stamp-not-at-the-end"),
    ],
)
def test_run_id_without_a_trailing_stamp_and_pid_has_no_start_stamp(run_id: str) -> None:
    assert start_stamp(run_id) is None


def test_window_ends_at_ts_and_starts_wall_seconds_earlier() -> None:
    window = run_window("run-plain", "2026-10-22T10:05:00+00:00", 100, None)

    assert window == RunWindow(start=at(10, 3, 20), end=at(10, 5, 0))


def test_run_id_start_stamp_wins_over_ts_minus_wall_seconds() -> None:
    window = run_window(
        "delegate-handoff-20261022T100000Z-4242", "2026-10-22T10:05:00+00:00", 100, 90
    )

    assert window == RunWindow(start=at(10, 0, 0), end=at(10, 5, 0))


def test_agent_seconds_is_used_when_wall_seconds_is_absent() -> None:
    window = run_window("run-plain", "2026-10-22T10:05:00+00:00", None, 42)

    assert window == RunWindow(start=at(10, 4, 18), end=at(10, 5, 0))


def test_fractional_wall_seconds_are_kept() -> None:
    window = run_window("run-plain", "2026-10-22T10:05:00+00:00", 12.5, None)

    assert window is not None
    assert window.end - window.start == timedelta(seconds=12.5)


def test_naive_ts_is_taken_as_utc() -> None:
    window = run_window("run-plain", "2026-10-22T10:05:00", 100, None)

    assert window is not None
    assert window.end.utcoffset() == timedelta(0)
    assert window.end == at(10, 5, 0)


def test_ts_with_an_offset_names_the_same_instant() -> None:
    window = run_window("run-plain", "2026-10-22T12:05:00+02:00", 100, None)

    assert window == RunWindow(start=at(10, 3, 20), end=at(10, 5, 0))


@pytest.mark.parametrize(
    ("run_id", "ts", "wall", "agent"),
    [
        pytest.param("run-plain", None, 100, 90, id="no-ts"),
        pytest.param("run-plain", "not a timestamp", 100, 90, id="unparsable-ts"),
        pytest.param("run-plain", "2026-10-22T10:05:00+00:00", None, None, id="no-start"),
        pytest.param(
            "delegate-handoff-20261022T110000Z-4242",
            "2026-10-22T10:05:00+00:00",
            100,
            None,
            id="stamp-after-ts",
        ),
        pytest.param(
            "delegate-handoff-20261022T100000Z-4242", None, None, None, id="stamp-but-no-ts"
        ),
    ],
)
def test_run_without_a_usable_window_gives_none(
    run_id: str, ts: str | None, wall: float | None, agent: float | None
) -> None:
    assert run_window(run_id, ts, wall, agent) is None


def test_widen_moves_each_edge_out_by_the_given_seconds() -> None:
    window = RunWindow(start=at(10, 0, 0), end=at(10, 1, 40))

    widened = widen(window, SCRAPE_INTERVAL_SECONDS)

    assert widened == RunWindow(start=at(9, 59, 30), end=at(10, 2, 10))


def test_widen_leaves_the_original_window_unchanged() -> None:
    window = RunWindow(start=at(10, 0, 0), end=at(10, 1, 40))

    widen(window, 30)

    assert window == RunWindow(start=at(10, 0, 0), end=at(10, 1, 40))


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        pytest.param((at(10, 0), at(10, 2)), (at(10, 1), at(10, 3)), True, id="partial"),
        pytest.param((at(10, 0), at(10, 5)), (at(10, 1), at(10, 2)), True, id="contained"),
        pytest.param((at(10, 0), at(10, 2)), (at(10, 2), at(10, 4)), False, id="back-to-back"),
        pytest.param((at(10, 0), at(10, 1)), (at(10, 3), at(10, 4)), False, id="apart"),
    ],
)
def test_overlap_is_strict_and_symmetric(
    first: tuple[datetime, datetime], second: tuple[datetime, datetime], expected: bool
) -> None:
    a = RunWindow(start=first[0], end=first[1])
    b = RunWindow(start=second[0], end=second[1])

    assert overlaps(a, b) is expected
    assert overlaps(b, a) is expected


def _agent_scrape_job() -> dict[str, object]:
    """Return the inference agent's single scrape job."""
    receivers = load_yaml_mapping(AGENT_CONFIG).get("receivers")
    assert isinstance(receivers, dict), f"expected receivers in {relative(AGENT_CONFIG)}"
    jobs = [
        job
        for name, receiver in receivers.items()
        if str(name).split("/")[0] == "prometheus" and isinstance(receiver, dict)
        for job in receiver["config"]["scrape_configs"]
    ]
    assert len(jobs) == 1, f"expected one scrape job in {relative(AGENT_CONFIG)}, got {jobs}"
    return jobs[0]


def test_scrape_interval_matches_the_inference_agent_config() -> None:
    interval = _agent_scrape_job()["scrape_interval"]

    assert SCRAPE_INTERVAL_SECONDS == 30
    assert f"{SCRAPE_INTERVAL_SECONDS}s" == interval


def test_service_name_matches_the_inference_agent_job_name() -> None:
    assert SERVICE_NAME == "inference-models"
    assert _agent_scrape_job()["job_name"] == SERVICE_NAME
