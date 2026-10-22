"""The run table's row: one delegate run, flattened from its ledger row and evidence."""

from dataclasses import dataclass

from agentic_telemetry.run_table.json_values import (
    JsonObject,
    as_json_text,
    as_object,
    object_list,
    opt_bool,
    opt_number,
    opt_str,
    str_list,
)
from agentic_telemetry.run_table.result_dir import StateSummary
from agentic_telemetry.run_table.session_usage import SessionUsage


@dataclass(frozen=True)
class RunRow:
    """One run-table row. Field order is the Parquet column order (spec 0001)."""

    # Identity
    run_id: str
    project: str
    ledger_path: str
    ts: str | None
    harness: str | None
    mode: str | None
    bucket: str | None
    target: str | None
    packet: str | None
    packet_sha256: str | None
    snapshot_sha256: str | None
    # Served configuration
    fingerprint: str | None
    qualified: bool | None
    model_name: str | None
    model_id: str | None
    quant: str | None
    runtime: str | None
    endpoint: str | None
    request_params: str | None
    # Outcome
    outcome: str | None
    reason: str | None
    gate: str | None
    unverified_steps: tuple[str, ...]
    attempts: int
    attempt_outcomes: tuple[str, ...]
    agent_seconds: int | float | None
    wall_seconds: int | float | None
    # Review
    review_verdict: str | None
    review_ts: str | None
    review_count: int
    # Derived failure
    delegated_phase: str | None
    first_failure_phase: str | None
    failure_cause: str | None
    # Session usage: null when the run has no transcript (or no state.json)
    tokens_in: int | None
    tokens_out: int | None
    tokens_cache_read: int | None
    tokens_reasoning: int | None
    assistant_turns: int | None
    compactions: int | None
    retries: int | None
    # Model names the run's own transcript recorded: null with no transcript (spec 0005).
    # `served_models` holds only the names the server answered with, which Pi records
    # only when they differ from what was requested.
    requested_models: tuple[str, ...] | None
    served_models: tuple[str, ...] | None
    # This run's own largest one-turn context, from its transcript.
    peak_turn_prompt_tokens: int | None
    # Pi subagents the run started (spec 0009): child runs, their turns and model
    # names, from the `subagent` tool results whose usage the token columns already
    # include (all but `tokens_reasoning`: Pi reports no reasoning there). Null with
    # no transcript; `subagent_turns` is also null when a usage-bearing call has no
    # integer `totalChildUsage.turns`. The child names stay out of
    # `requested_models`, so the join resolves the parent's.
    subagent_runs: int | None
    subagent_turns: int | None
    subagent_models: tuple[str, ...] | None
    # Server-side columns, filled by the metrics join: `not_queried` and nulls until
    # `run-table --metrics-url` has run.
    server_status: str = "not_queried"
    server_model_id: str | None = None
    server_model_source: str | None = None
    server_token_match: str | None = None
    server_prompt_tokens: int | None = None
    server_predicted_tokens: int | None = None
    server_prefill_tps: float | None = None
    server_decode_tps: float | None = None
    server_deferred_max: int | None = None
    # The largest context since the model loaded, not this run's own: the server
    # counter is a high-water mark and may come from an earlier run on the same
    # loaded model. A run's own peak context is `peak_turn_prompt_tokens`.
    server_ctx_high_water: int | None = None
    server_cold_start: bool | None = None


@dataclass(frozen=True)
class ReviewSummary:
    """The review that decides a run's verdict, and how many reviews the run had."""

    verdict: str | None
    ts: str | None
    count: int


@dataclass(frozen=True)
class FailureColumns:
    """The derived failure columns of one run."""

    delegated_phase: str | None
    first_failure_phase: str | None
    failure_cause: str | None


def build_row(
    *,
    project: str,
    ledger_path: str,
    run: JsonObject,
    review: ReviewSummary | None,
    usage: SessionUsage | None,
    state: StateSummary | None,
    failure: FailureColumns,
) -> RunRow:
    """Flatten one ledger `run` row and its joined evidence into a `RunRow`.

    Args:
        project: The project the ledger belongs to.
        ledger_path: The ledger the row was read from.
        run: The ledger `run` row (or the `result.json` that stood in for it).
        review: The run's latest review, or None when it has none.
        usage: Token totals from the run's transcripts, or None.
        state: The run's `state.json` summary, or None.
        failure: The derived failure columns.
    """
    model = as_object(run.get("model")) or {}
    attempts = object_list(run.get("attempts"))
    seconds = [opt_number(attempt.get("seconds")) for attempt in attempts]
    recorded_seconds = [value for value in seconds if value is not None]
    return RunRow(
        run_id=opt_str(run.get("run")) or "",
        project=project,
        ledger_path=ledger_path,
        ts=opt_str(run.get("ts")),
        harness=opt_str(run.get("harness")),
        mode=opt_str(run.get("mode")),
        bucket=opt_str(run.get("bucket")),
        target=opt_str(run.get("target")),
        packet=opt_str(run.get("packet")),
        packet_sha256=opt_str(run.get("packet_sha256")),
        snapshot_sha256=opt_str(run.get("snapshot_sha256")),
        fingerprint=opt_str(run.get("fingerprint")),
        qualified=opt_bool(run.get("qualified")),
        model_name=opt_str(model.get("name")),
        model_id=opt_str(model.get("model_id")),
        quant=opt_str(model.get("quant")),
        runtime=opt_str(model.get("runtime")),
        endpoint=opt_str(model.get("endpoint")),
        request_params=as_json_text(model.get("request")),
        outcome=opt_str(run.get("outcome")),
        reason=as_json_text(run.get("reason")),
        gate=as_json_text(run.get("gate")),
        unverified_steps=str_list(run.get("unverified_steps")),
        attempts=len(attempts),
        attempt_outcomes=tuple(as_json_text(attempt.get("outcome")) or "" for attempt in attempts),
        agent_seconds=sum(recorded_seconds) if recorded_seconds else None,
        wall_seconds=opt_number(run.get("wall_seconds")),
        review_verdict=review.verdict if review else None,
        review_ts=review.ts if review else None,
        review_count=review.count if review else 0,
        delegated_phase=failure.delegated_phase,
        first_failure_phase=failure.first_failure_phase,
        failure_cause=failure.failure_cause,
        tokens_in=usage.tokens_in if usage else None,
        tokens_out=usage.tokens_out if usage else None,
        tokens_cache_read=usage.tokens_cache_read if usage else None,
        tokens_reasoning=usage.tokens_reasoning if usage else None,
        assistant_turns=usage.assistant_turns if usage else None,
        compactions=state.compactions if state else None,
        retries=state.retries if state else None,
        requested_models=usage.requested_models if usage else None,
        served_models=usage.served_models if usage else None,
        peak_turn_prompt_tokens=usage.peak_turn_prompt_tokens if usage else None,
        subagent_runs=usage.subagent_runs if usage else None,
        subagent_turns=usage.subagent_turns if usage else None,
        subagent_models=usage.subagent_models if usage else None,
    )
