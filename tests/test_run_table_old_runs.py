"""Runs without a subagent keep every pre-0009 column value (spec 0009, Old runs).

The golden below was captured from the run table before spec 0009 changed it,
over the `evidence_tree` fixture, with `ledger_path` made relative to its root.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from run_table_builders import EvidenceTree

from agentic_telemetry.run_table.build import build_run_table
from agentic_telemetry.run_table.row import RunRow

NEW_IN_0009 = frozenset({"subagent_runs", "subagent_turns", "subagent_models"})

# Values every one of the five rows shares.
COMMON: dict[str, object] = {
    "ts": "2026-10-20T10:00:00+00:00",
    "target": "src/example_module.py",
    "packet": "packets/example-packet.md",
    "packet_sha256": "a" * 64,
    "snapshot_sha256": "b" * 64,
    "fingerprint": "fp-0a1b2c3d4e5f",
    "qualified": True,
    "model_id": "example-coder-7b-instruct",
    "quant": "q4_k_m",
    "runtime": "example-runtime",
    "endpoint": "gpu.example",
    "request_params": '{"temperature":0.2, "top_p": 0.95}',
    "reason": "reason-text-not-for-stdout",
    "gate": "pass",
    "unverified_steps": ("manual smoke check",),
    "attempts": 2,
    "attempt_outcomes": ("gate-fail", "green"),
    "agent_seconds": 42,
    "delegated_phase": "implement",
    "server_status": "not_queried",
    "server_model_id": None,
    "server_model_source": None,
    "server_token_match": None,
    "server_prompt_tokens": None,
    "server_predicted_tokens": None,
    "server_prefill_tps": None,
    "server_decode_tps": None,
    "server_deferred_max": None,
    "server_ctx_high_water": None,
    "server_cold_start": None,
}

# Values the four alpha rows share: bootstrapped layout, no transcript, no state.json.
ALPHA: dict[str, object] = {
    "project": "alpha",
    "ledger_path": "alpha/.agentic/delegate/ledger.jsonl",
    "harness": "delegate-run-v1",
    "mode": "per-file",
    "bucket": "small",
    "model_name": "example-coder",
    "tokens_in": None,
    "tokens_out": None,
    "tokens_cache_read": None,
    "tokens_reasoning": None,
    "assistant_turns": None,
    "compactions": None,
    "retries": None,
    "requested_models": None,
    "served_models": None,
    "peak_turn_prompt_tokens": None,
}

PER_RUN: dict[str, dict[str, object]] = {
    "alpha-run-green": {
        **ALPHA,
        "outcome": "green",
        "wall_seconds": 50,
        "review_verdict": "pass",
        "review_ts": "2026-10-20T12:00:00+00:00",
        "review_count": 1,
        "first_failure_phase": None,
        "failure_cause": None,
    },
    "alpha-run-defect": {
        **ALPHA,
        "outcome": "green",
        "wall_seconds": 50,
        "review_verdict": "model-defect",
        "review_ts": "2026-10-20T12:00:00+00:00",
        "review_count": 1,
        "first_failure_phase": "review",
        "failure_cause": "model-defect",
    },
    "alpha-run-frontier": {
        **ALPHA,
        "outcome": "needs_frontier",
        "wall_seconds": None,
        "review_verdict": None,
        "review_ts": None,
        "review_count": 0,
        "first_failure_phase": "gate",
        "failure_cause": "needs_frontier",
    },
    "alpha-run-from-result": {
        **ALPHA,
        "outcome": "integrity",
        "wall_seconds": 50,
        "review_verdict": None,
        "review_ts": None,
        "review_count": 0,
        "first_failure_phase": "implement",
        "failure_cause": "integrity",
    },
    "beta-run-handoff": {
        "project": "beta",
        "ledger_path": "landing/laptop.example/beta/ledger.jsonl",
        "harness": "delegate-run-v2",
        "mode": "handoff",
        "bucket": "phase:implement",
        "model_name": "example-planner",
        "outcome": "integrity",
        "wall_seconds": 50,
        "review_verdict": "packet-defect",
        "review_ts": "2026-10-20T12:00:00+00:00",
        "review_count": 1,
        "first_failure_phase": "implement",
        "failure_cause": "integrity",
        "tokens_in": 1000,
        "tokens_out": 200,
        "tokens_cache_read": 50,
        "tokens_reasoning": 0,
        "assistant_turns": 1,
        "compactions": 3,
        "retries": 1,
        "requested_models": (),
        "served_models": (),
        "peak_turn_prompt_tokens": 1050,
    },
}

GOLDEN: dict[str, dict[str, object]] = {
    run_id: {"run_id": run_id, **COMMON, **values} for run_id, values in PER_RUN.items()
}


def rows_by_id(tree: EvidenceTree) -> dict[str, RunRow]:
    """Build the run table over the fixture and key its rows by run ID."""
    rows, _ = build_run_table([tree.root])
    return {row.run_id: row for row in rows}


def pre_0009_columns(row: RunRow, root: Path) -> dict[str, object]:
    """Return every column of `row` that existed before spec 0009, by name.

    `ledger_path` is made relative to `root`, the fixture's temporary directory.
    """
    values = {
        field.name: getattr(row, field.name)
        for field in dataclasses.fields(row)
        if field.name not in NEW_IN_0009
    }
    values["ledger_path"] = Path(str(values["ledger_path"])).relative_to(root).as_posix()
    return values


def test_golden_covers_every_pre_0009_column() -> None:
    names = {field.name for field in dataclasses.fields(RunRow)} - NEW_IN_0009
    for run_id, golden in GOLDEN.items():
        assert set(golden) == names, run_id


def test_rows_without_a_subagent_keep_every_pre_0009_value(evidence_tree: EvidenceTree) -> None:
    rows = rows_by_id(evidence_tree)

    assert sorted(rows) == sorted(GOLDEN)
    for run_id, golden in GOLDEN.items():
        assert pre_0009_columns(rows[run_id], evidence_tree.root) == golden, run_id


def test_transcript_without_a_subagent_reads_zero_subagent_runs_and_turns(
    evidence_tree: EvidenceTree,
) -> None:
    row = rows_by_id(evidence_tree)["beta-run-handoff"]

    assert (row.subagent_runs, row.subagent_turns, row.subagent_models) == (0, 0, ())


def test_runs_without_a_transcript_read_null_subagent_runs_and_turns(
    evidence_tree: EvidenceTree,
) -> None:
    rows = rows_by_id(evidence_tree)

    for run_id in evidence_tree.alpha_run_ids:
        row = rows[run_id]
        assert (row.subagent_runs, row.subagent_turns, row.subagent_models) == (
            None,
            None,
            None,
        ), run_id
