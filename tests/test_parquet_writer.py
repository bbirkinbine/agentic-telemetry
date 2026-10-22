"""Parquet output: column order, types, nulls, overwrite and the empty-table schema."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import duckdb
from run_table_builders import PLACEHOLDER_ENDPOINT

from agentic_telemetry.run_table.parquet_writer import RUN_TABLE_SCHEMA, write_run_table
from agentic_telemetry.run_table.row import RunRow

EXPECTED_COLUMNS = [
    "run_id",
    "project",
    "ledger_path",
    "ts",
    "harness",
    "mode",
    "bucket",
    "target",
    "packet",
    "packet_sha256",
    "snapshot_sha256",
    "fingerprint",
    "qualified",
    "model_name",
    "model_id",
    "quant",
    "runtime",
    "endpoint",
    "request_params",
    "outcome",
    "reason",
    "gate",
    "unverified_steps",
    "attempts",
    "attempt_outcomes",
    "agent_seconds",
    "wall_seconds",
    "review_verdict",
    "review_ts",
    "review_count",
    "delegated_phase",
    "first_failure_phase",
    "failure_cause",
    "tokens_in",
    "tokens_out",
    "tokens_cache_read",
    "tokens_reasoning",
    "assistant_turns",
    "compactions",
    "retries",
    # Spec 0005: appended after 0001's columns, which stay an unchanged prefix.
    "requested_models",
    "served_models",
    "peak_turn_prompt_tokens",
    "server_status",
    "server_model_id",
    "server_model_source",
    "server_token_match",
    "server_prompt_tokens",
    "server_predicted_tokens",
    "server_prefill_tps",
    "server_decode_tps",
    "server_deferred_max",
    "server_ctx_high_water",
    "server_cold_start",
]

INTEGER_COLUMNS = [
    "attempts",
    "review_count",
    "tokens_in",
    "tokens_out",
    "tokens_cache_read",
    "tokens_reasoning",
    "assistant_turns",
    "compactions",
    "retries",
    "peak_turn_prompt_tokens",
    "server_prompt_tokens",
    "server_predicted_tokens",
    "server_deferred_max",
    "server_ctx_high_water",
]


def sample_row(run_id: str = "run-a", **overrides: object) -> RunRow:
    values: dict[str, object] = {
        "run_id": run_id,
        "project": "local-game",
        "ledger_path": "evidence/local-game/.agentic/delegate/ledger.jsonl",
        "ts": "2026-10-20T10:00:00+00:00",
        "harness": "delegate-run-v2",
        "mode": "handoff",
        "bucket": "phase:implement",
        "target": "src/example_module.py",
        "packet": "packets/example-packet.md",
        "packet_sha256": "a" * 64,
        "snapshot_sha256": "b" * 64,
        "fingerprint": "fp-0a1b2c3d4e5f",
        "qualified": True,
        "model_name": "example-coder",
        "model_id": "example-coder-7b-instruct",
        "quant": "q4_k_m",
        "runtime": "example-runtime",
        "endpoint": PLACEHOLDER_ENDPOINT,
        "request_params": '{"temperature":0.2}',
        "outcome": "integrity",
        "reason": "reason-text",
        "gate": "fail",
        "unverified_steps": ["manual smoke check", "visual check"],
        "attempts": 2,
        "attempt_outcomes": ["gate-fail", "integrity"],
        "agent_seconds": 42,
        "wall_seconds": None,
        "review_verdict": "packet-defect",
        "review_ts": "2026-10-20T12:00:00+00:00",
        "review_count": 1,
        "delegated_phase": "implement",
        "first_failure_phase": "implement",
        "failure_cause": "integrity",
        "tokens_in": 1200,
        "tokens_out": 120,
        "tokens_cache_read": 60,
        "tokens_reasoning": 11,
        "assistant_turns": 2,
        "compactions": 2,
        "retries": 1,
        "requested_models": ["local-qwen38-27b"],
        "served_models": ["qwen3.8-27b-ud-q8_k_xl"],
        "peak_turn_prompt_tokens": 1260,
        "server_status": "joined",
        "server_model_id": "qwen3.8-27b-ud-q8_k_xl",
        "server_model_source": "served",
        "server_token_match": "exact",
        "server_prompt_tokens": 1200,
        "server_predicted_tokens": 120,
        "server_prefill_tps": 250.5,
        "server_decode_tps": 20.25,
        "server_deferred_max": 0,
        "server_ctx_high_water": 8192,
        "server_cold_start": True,
    }
    values.update(overrides)
    return RunRow(**values)


def _read(path: Path) -> tuple[list[str], dict[str, str], list[dict[str, object]]]:
    relation = duckdb.read_parquet(str(path))
    columns = list(relation.columns)
    types = {name: str(kind) for name, kind in zip(columns, relation.types, strict=True)}
    records = [dict(zip(columns, values, strict=True)) for values in relation.fetchall()]
    return columns, types, records


def test_run_row_fields_are_the_spec_column_order() -> None:
    assert [field.name for field in dataclasses.fields(RunRow)] == EXPECTED_COLUMNS


def test_schema_constant_matches_run_row_fields() -> None:
    assert [name for name, _ in RUN_TABLE_SCHEMA] == EXPECTED_COLUMNS


def test_written_file_has_exact_columns_in_order_and_one_row_per_run(tmp_path: Path) -> None:
    out = tmp_path / "run_table.parquet"

    write_run_table([sample_row("run-a"), sample_row("run-b")], out)

    columns, _, records = _read(out)
    assert columns == EXPECTED_COLUMNS
    assert [record["run_id"] for record in records] == ["run-a", "run-b"]


def test_values_round_trip_with_lists_bools_and_nulls(tmp_path: Path) -> None:
    out = tmp_path / "run_table.parquet"

    write_run_table([sample_row("run-a")], out)

    _, _, (record,) = _read(out)
    assert record["unverified_steps"] == ["manual smoke check", "visual check"]
    assert record["attempt_outcomes"] == ["gate-fail", "integrity"]
    assert record["qualified"] is True
    assert record["wall_seconds"] is None
    assert record["agent_seconds"] == 42
    assert record["tokens_in"] == 1200
    assert record["request_params"] == '{"temperature":0.2}'
    assert record["endpoint"] == PLACEHOLDER_ENDPOINT


def test_column_types_are_fixed_by_the_explicit_schema(tmp_path: Path) -> None:
    out = tmp_path / "run_table.parquet"

    write_run_table([sample_row("run-a")], out)

    _, types, _ = _read(out)
    assert types["unverified_steps"] == "VARCHAR[]"
    assert types["attempt_outcomes"] == "VARCHAR[]"
    assert types["qualified"] == "BOOLEAN"
    for name in INTEGER_COLUMNS:
        assert types[name] == "BIGINT", name
    for name in ("agent_seconds", "wall_seconds", "server_prefill_tps", "server_decode_tps"):
        assert types[name] == "DOUBLE", name
    for name in ("run_id", "ts", "review_ts", "request_params", "gate", "failure_cause"):
        assert types[name] == "VARCHAR", name
    for name in ("server_status", "server_model_id", "server_model_source", "server_token_match"):
        assert types[name] == "VARCHAR", name
    assert types["requested_models"] == types["served_models"] == "VARCHAR[]"
    assert types["server_cold_start"] == "BOOLEAN"


def test_server_and_transcript_columns_round_trip(tmp_path: Path) -> None:
    out = tmp_path / "run_table.parquet"

    write_run_table([sample_row("run-a")], out)

    _, _, (record,) = _read(out)
    assert record["requested_models"] == ["local-qwen38-27b"]
    assert record["served_models"] == ["qwen3.8-27b-ud-q8_k_xl"]
    assert record["peak_turn_prompt_tokens"] == 1260
    assert record["server_status"] == "joined"
    assert record["server_prompt_tokens"] == 1200
    assert record["server_prefill_tps"] == 250.5
    assert record["server_cold_start"] is True


def test_all_null_columns_keep_their_types(tmp_path: Path) -> None:
    out = tmp_path / "run_table.parquet"
    row = sample_row(
        "run-per-file",
        tokens_in=None,
        tokens_out=None,
        tokens_cache_read=None,
        tokens_reasoning=None,
        assistant_turns=None,
        compactions=None,
        retries=None,
        review_verdict=None,
        review_ts=None,
    )

    write_run_table([row], out)

    _, types, (record,) = _read(out)
    assert record["tokens_in"] is None
    assert types["tokens_in"] == "BIGINT"
    assert types["retries"] == "BIGINT"
    assert types["review_ts"] == "VARCHAR"


def test_empty_table_still_writes_the_full_schema(tmp_path: Path) -> None:
    out = tmp_path / "run_table.parquet"

    write_run_table([], out)

    columns, types, records = _read(out)
    assert columns == EXPECTED_COLUMNS
    assert records == []
    assert types["unverified_steps"] == "VARCHAR[]"
    assert types["qualified"] == "BOOLEAN"
    assert types["tokens_in"] == "BIGINT"


def test_existing_output_file_is_replaced(tmp_path: Path) -> None:
    out = tmp_path / "run_table.parquet"
    write_run_table([sample_row("run-a"), sample_row("run-b"), sample_row("run-c")], out)

    write_run_table([sample_row("run-z")], out)

    _, _, records = _read(out)
    assert [record["run_id"] for record in records] == ["run-z"]


def test_write_leaves_only_the_output_file_behind(tmp_path: Path) -> None:
    out_dir = tmp_path / "data"
    out_dir.mkdir()
    out = out_dir / "run_table.parquet"

    write_run_table([sample_row("run-a")], out)

    assert sorted(path.name for path in out_dir.iterdir()) == ["run_table.parquet"]
