"""Derived failure columns: delegated phase and first failure phase plus cause."""

from __future__ import annotations

import pytest

from agentic_telemetry.run_table.failure_phase import delegated_phase, first_failure


@pytest.mark.parametrize(
    ("outcome", "verdict", "phase", "expected"),
    [
        pytest.param("green", "pass", "implement", (None, None), id="green-review-pass"),
        pytest.param("green", None, "implement", (None, None), id="green-no-review"),
        pytest.param(
            "green", "model-defect", "implement", ("review", "model-defect"), id="green-model"
        ),
        pytest.param(
            "green", "packet-defect", "plan", ("review", "packet-defect"), id="green-packet"
        ),
        pytest.param(
            "needs_frontier", None, "implement", ("gate", "needs_frontier"), id="needs-frontier"
        ),
        pytest.param(
            "needs_frontier",
            "model-defect",
            "implement",
            ("gate", "needs_frontier"),
            id="needs-frontier-reviewed",
        ),
        pytest.param(
            "integrity", None, "implement", ("implement", "integrity"), id="integrity-implement"
        ),
        pytest.param(
            "integrity", "pass", "test-first", ("test-first", "integrity"), id="integrity-phase"
        ),
    ],
)
def test_first_failure_follows_the_outcome_and_verdict_table(
    outcome: str,
    verdict: str | None,
    phase: str,
    expected: tuple[str | None, str | None],
) -> None:
    failure_phase, cause, warning = first_failure(outcome, verdict, phase)

    assert (failure_phase, cause) == expected
    assert warning is None


def test_unknown_outcome_is_phase_unknown_with_the_outcome_as_cause_and_a_warning() -> None:
    failure_phase, cause, warning = first_failure("timeout", None, "implement")

    assert failure_phase == "unknown"
    assert cause == "timeout"
    assert warning is not None
    assert "timeout" in warning


@pytest.mark.parametrize("bucket", ["small", "medium"])
def test_per_file_bucket_is_delegated_phase_implement(bucket: str) -> None:
    phase, warning = delegated_phase(bucket, None)

    assert phase == "implement"
    assert warning is None


def test_phase_bucket_prefix_gives_the_delegated_phase() -> None:
    assert delegated_phase("phase:test-first", None) == ("test-first", None)


def test_state_json_phase_wins_over_the_bucket_prefix() -> None:
    assert delegated_phase("phase:plan", "implement") == ("implement", None)


def test_state_json_phase_is_used_when_bucket_is_missing() -> None:
    assert delegated_phase(None, "review") == ("review", None)


def test_unrecognized_bucket_without_state_gives_null_with_a_warning() -> None:
    phase, warning = delegated_phase("large", None)

    assert phase is None
    assert warning is not None
    assert "large" in warning


def test_phase_outside_the_transition_matrix_is_kept_as_recorded_with_a_warning() -> None:
    phase, warning = delegated_phase("phase:deploy", None)

    assert phase == "deploy"
    assert warning is not None
    assert "deploy" in warning


@pytest.mark.parametrize(
    "phase_name", ["spec", "plan", "test-first", "implement", "gate", "review"]
)
def test_every_transition_matrix_phase_is_accepted_without_warning(phase_name: str) -> None:
    assert delegated_phase(f"phase:{phase_name}", None) == (phase_name, None)


def test_green_reviewed_without_a_verdict_is_unknown_with_a_warning() -> None:
    failure_phase, cause, warning = first_failure("green", None, "implement", reviewed=True)

    assert (failure_phase, cause) == ("unknown", "review-without-verdict")
    assert warning is not None


def test_integrity_with_an_underivable_phase_is_unknown() -> None:
    assert first_failure("integrity", None, None) == ("unknown", "integrity", None)
