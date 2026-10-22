"""Server columns filled for the resolved model (spec 0005, Columns; Missing metrics; D3)."""

from __future__ import annotations

import dataclasses

import pytest
from server_join_helpers import (
    ALIAS_MAYA,
    ALIAS_QWEN,
    BASE_URL,
    FLASH,
    GLM,
    KNOWN,
    NOW,
    QWEN,
    merge_readings,
    model_readings,
    server_row,
)
from vm_stub import UP_AT_START, UP_LATER, FakeVictoria, Readings

from agentic_telemetry.run_table.row import RunRow
from agentic_telemetry.server_metrics.join import join_server_metrics


def join_one(fake: FakeVictoria, **overrides: object) -> RunRow:
    """Join one default row (with overrides) and return the joined row."""
    (row,), _ = join_server_metrics([server_row(**overrides)], BASE_URL, fake, NOW)
    return row


def test_token_and_throughput_columns_come_from_the_resolved_models_increases() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())

    row = join_one(fake)

    assert row.server_prompt_tokens == 1000
    assert row.server_predicted_tokens == 200
    assert row.server_prefill_tps == pytest.approx(250.0)
    assert row.server_decode_tps == pytest.approx(20.0)
    assert row.server_deferred_max == 3
    assert row.server_ctx_high_water == 8192


def test_columns_ignore_other_models_in_the_same_vectors() -> None:
    other = model_readings(FLASH, prompt=50000.0, predicted=9000.0, ctx=131072.0, deferred=7.0)
    fake = FakeVictoria(known=KNOWN, readings=merge_readings(model_readings(QWEN), other))

    row = join_one(fake)

    assert row.server_model_id == QWEN
    assert (row.server_prompt_tokens, row.server_predicted_tokens) == (1000, 200)
    assert row.server_ctx_high_water == 8192
    assert row.server_deferred_max == 3


def test_server_token_columns_are_rounded_integers() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings(prompt=1199.6, predicted=200.4))

    row = join_one(fake, tokens_in=None, tokens_out=None)

    assert row.server_prompt_tokens == 1200
    assert row.server_predicted_tokens == 200
    assert isinstance(row.server_prompt_tokens, int)
    assert isinstance(row.server_predicted_tokens, int)
    assert isinstance(row.server_deferred_max, int)
    assert isinstance(row.server_ctx_high_water, int)


def test_zero_prompt_seconds_gives_null_prefill_and_keeps_decode() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings(prompt_seconds=0.0))

    row = join_one(fake)

    assert row.server_prefill_tps is None
    assert row.server_decode_tps == pytest.approx(20.0)
    assert row.server_status == "joined"


def test_zero_predicted_seconds_gives_null_decode() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings(predicted_seconds=0.0))

    row = join_one(fake)

    assert row.server_decode_tps is None
    assert row.server_prefill_tps == pytest.approx(250.0)


def test_absent_seconds_counters_give_null_throughput() -> None:
    readings = model_readings()
    readings["llamacpp_prompt_seconds_total"] = {}
    readings["llamacpp_tokens_predicted_seconds_total"] = {}
    fake = FakeVictoria(known=KNOWN, readings=readings)

    row = join_one(fake)

    assert (row.server_prefill_tps, row.server_decode_tps) == (None, None)
    assert row.server_status == "joined"


def test_build_without_ctx_metric_gives_null_ctx_and_unchanged_status() -> None:
    """Strata serves no `n_tokens_max`."""
    fake = FakeVictoria(known=KNOWN, readings=model_readings(ctx=None))

    row = join_one(fake)

    assert row.server_ctx_high_water is None
    assert row.server_status == "joined"
    assert row.server_prompt_tokens == 1000


def test_build_without_deferred_metric_gives_null_deferred_and_unchanged_status() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings(deferred=None))

    row = join_one(fake)

    assert row.server_deferred_max is None
    assert row.server_status == "joined"


@pytest.mark.parametrize(
    ("up_at_start", "up_later", "expected"),
    [
        pytest.param(None, 1.0, True, id="absent-at-start-then-up"),
        pytest.param(0.0, 1.0, True, id="down-at-start-then-up"),
        pytest.param(1.0, 1.0, False, id="already-up"),
        pytest.param(0.0, 0.0, False, id="down-throughout"),
        pytest.param(None, None, None, id="no-up-sample-is-unknown"),
    ],
)
def test_cold_start_is_up_absent_or_zero_at_start_and_one_later(
    up_at_start: float | None, up_later: float | None, expected: bool | None
) -> None:
    """D8: `up` later is read through the widened end; no `up` sample at all is null."""
    readings = model_readings(up_at_start=up_at_start, up_later=up_later)
    fake = FakeVictoria(known=KNOWN, readings=readings)

    row = join_one(fake)

    assert row.server_status == "joined"
    assert row.server_cold_start is expected


@pytest.mark.parametrize(
    ("overrides", "status"),
    [
        pytest.param({"requested_models": (QWEN,), "served_models": ()}, "no_data", id="no-data"),
        pytest.param(
            {"requested_models": ("local-unknown",), "served_models": (), "model_id": None},
            "uncovered",
            id="uncovered",
        ),
    ],
)
def test_cold_start_is_null_when_the_run_did_not_join(
    overrides: dict[str, object], status: str
) -> None:
    readings: Readings = {UP_AT_START: {}, UP_LATER: {QWEN: 1.0}}
    fake = FakeVictoria(known=KNOWN, readings=readings)

    row = join_one(fake, **overrides)

    assert row.server_status == status
    assert row.server_cold_start is None


def test_run_without_transcript_names_is_inferred_from_the_one_model_that_moved() -> None:
    """D8: rule 3 applies only when the transcript recorded no model names."""
    fake = FakeVictoria(known=KNOWN, readings=model_readings(FLASH))

    row = join_one(fake, requested_models=(), served_models=(), model_id=ALIAS_QWEN)

    assert (row.server_model_id, row.server_model_source) == (FLASH, "inferred")
    assert row.server_status == "joined"
    assert [row.server_token_match] == ["exact"]


def test_inferred_model_is_kept_only_when_tokens_reconcile_exactly() -> None:
    """D8: a neighbour's moving counters must not pin an uncovered run to its model."""
    fake = FakeVictoria(known=KNOWN, readings=model_readings(FLASH, prompt=40.0, predicted=5.0))

    row = join_one(fake, requested_models=(), served_models=(), model_id=ALIAS_QWEN)

    assert (row.server_model_id, row.server_model_source) == (None, "none")
    assert row.server_status == "uncovered"
    assert row.server_prompt_tokens is None


def test_uncovered_names_are_not_inferred_onto_a_moving_neighbour() -> None:
    """D8: a run on a build without metrics (its names unknown) next to a moving Qwen."""
    fake = FakeVictoria(known=(QWEN, FLASH), readings=model_readings(QWEN))

    row = join_one(fake, requested_models=(ALIAS_MAYA,), served_models=(GLM,), model_id=ALIAS_MAYA)

    assert (row.server_model_id, row.server_model_source) == (None, "none")
    assert row.server_status == "uncovered"


def test_two_models_moving_with_no_known_name_is_uncovered() -> None:
    readings = merge_readings(model_readings(QWEN), model_readings(FLASH))
    fake = FakeVictoria(known=KNOWN, readings=readings)

    row = join_one(fake, requested_models=(), served_models=(), model_id=ALIAS_QWEN)

    assert (row.server_model_id, row.server_model_source) == (None, "none")
    assert row.server_status == "uncovered"


def test_echoed_alias_falls_to_the_ledger_model_id() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings(QWEN))

    row = join_one(fake, requested_models=(ALIAS_QWEN,), served_models=(ALIAS_QWEN,), model_id=QWEN)

    assert (row.server_model_id, row.server_model_source) == (QWEN, "requested")


def test_echoed_alias_with_an_alias_in_the_ledger_is_uncovered() -> None:
    """D8: the echoed alias is a recorded name, so rule 3 does not apply."""
    fake = FakeVictoria(known=KNOWN, readings=model_readings(QWEN))

    row = join_one(
        fake, requested_models=(ALIAS_QWEN,), served_models=(ALIAS_QWEN,), model_id=ALIAS_QWEN
    )

    assert (row.server_model_id, row.server_model_source) == (None, "none")


def test_join_changes_only_server_columns() -> None:
    fake = FakeVictoria(known=KNOWN, readings=model_readings())
    before = server_row()

    (after,), _ = join_server_metrics([before], BASE_URL, fake, NOW)

    for field in dataclasses.fields(RunRow):
        if not field.name.startswith("server_"):
            assert getattr(after, field.name) == getattr(before, field.name), field.name
