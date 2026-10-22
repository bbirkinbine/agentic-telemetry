"""Synthetic rows, readings and evidence for the server-metrics join tests (spec 0005).

Model IDs are public llama-swap IDs already in tracked files, or `local-*`
profile-alias placeholders. Token counts and metric values are made up. The
VictoriaMetrics base URL is `telemetry.example` (RFC 2606).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from run_table_builders import (
    assistant_message,
    make_project,
    model_record,
    run_record,
    session_header,
    write_ledger,
    write_result_dir,
    write_session,
)
from vm_stub import (
    CTX,
    DEFERRED,
    PREDICTED,
    PREDICTED_SECONDS,
    PROMPT,
    PROMPT_SECONDS,
    UP_AT_START,
    UP_LATER,
)

from agentic_telemetry.run_table.row import RunRow

BASE_URL = "http://telemetry.example:8428"
NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)

QWEN = "qwen3.8-27b-ud-q8_k_xl"
FLASH = "qwen3.8-flash-next-unsloth-q8_0"
GLM = "glm-5.3-flash"
MAYA = "glm-5.3-flash-maya-s24"
ALIAS_QWEN = "local-qwen38-27b"
ALIAS_MAYA = "local-glm53-maya-l"
ALIAS_UNKNOWN = "local-glm53-sybil"
KNOWN = (QWEN, FLASH, GLM, MAYA)

# The default row's unwidened window: [10:00:00, 10:01:40] on 2026-10-22 (100 s).
RUN_START = datetime(2026, 10, 22, 10, 0, 0, tzinfo=UTC)
RUN_END = datetime(2026, 10, 22, 10, 1, 40, tzinfo=UTC)
# `write_server_project`'s joined run: [10:00:00, 10:01:40] on 2026-10-20.
PROJECT_JOINED_WINDOW = (
    datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC),
    datetime(2026, 10, 20, 10, 1, 40, tzinfo=UTC),
)

SERVER_NULL_COLUMNS = (
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
)
SERVER_VALUE_COLUMNS = (
    "server_prompt_tokens",
    "server_predicted_tokens",
    "server_prefill_tps",
    "server_decode_tps",
    "server_deferred_max",
    "server_ctx_high_water",
    "server_cold_start",
)


def server_row(run_id: str = "run-server-a", **overrides: object) -> RunRow:
    """Return a RunRow for one delegate run on the default window, before any join.

    Defaults: requested the alias, served the Qwen ID, 1000 tokens in and 200 out,
    `ts` 10:01:40 with `wall_seconds` 100, so the window is [10:00:00, 10:01:40].
    """
    values: dict[str, object] = {
        "run_id": run_id,
        "project": "local-game",
        "ledger_path": "evidence/local-game/.agentic/delegate/ledger.jsonl",
        "ts": "2026-10-22T10:01:40+00:00",
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
        "model_id": ALIAS_QWEN,
        "quant": "q8_0",
        "runtime": "example-runtime",
        "endpoint": "gpu.example",
        "request_params": None,
        "outcome": "green",
        "reason": None,
        "gate": "pass",
        "unverified_steps": (),
        "attempts": 1,
        "attempt_outcomes": ("green",),
        "agent_seconds": 90,
        "wall_seconds": 100,
        "review_verdict": None,
        "review_ts": None,
        "review_count": 0,
        "delegated_phase": "implement",
        "first_failure_phase": None,
        "failure_cause": None,
        "tokens_in": 1000,
        "tokens_out": 200,
        "tokens_cache_read": 0,
        "tokens_reasoning": 0,
        "assistant_turns": 2,
        "compactions": 0,
        "retries": 0,
        "requested_models": (ALIAS_QWEN,),
        "served_models": (QWEN,),
        "peak_turn_prompt_tokens": 700,
        "subagent_runs": 0,
        "subagent_turns": 0,
        "subagent_models": (),
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
    values.update(overrides)
    return RunRow(**values)


def model_readings(
    model_id: str = QWEN,
    *,
    prompt: float = 1000.0,
    predicted: float = 200.0,
    prompt_seconds: float = 4.0,
    predicted_seconds: float = 10.0,
    deferred: float | None = 3.0,
    ctx: float | None = 8192.0,
    up_at_start: float | None = 1.0,
    up_later: float | None = 1.0,
) -> dict[str, dict[str, float | str]]:
    """Return stub readings for one model; a None metric is left out of its vector.

    Defaults give prefill 250 tok/s (1000 / 4) and decode 20 tok/s (200 / 10).
    """
    readings: dict[str, dict[str, float | str]] = {
        PROMPT: {model_id: prompt},
        PREDICTED: {model_id: predicted},
        PROMPT_SECONDS: {model_id: prompt_seconds},
        PREDICTED_SECONDS: {model_id: predicted_seconds},
    }
    for key, value in (
        (DEFERRED, deferred),
        (CTX, ctx),
        (UP_AT_START, up_at_start),
        (UP_LATER, up_later),
    ):
        readings[key] = {} if value is None else {model_id: value}
    return readings


def merge_readings(
    *parts: dict[str, dict[str, float | str]],
) -> dict[str, dict[str, float | str]]:
    """Merge per-model readings into one set of vectors."""
    merged: dict[str, dict[str, float | str]] = {}
    for part in parts:
        for key, vector in part.items():
            merged.setdefault(key, {}).update(vector)
    return merged


def write_server_project(root: Path) -> Path:
    """Write a one-project evidence tree for the CLI and build tests; return its ledger.

    The runs are dated 2026-10-20, so a CLI run, which uses the real clock, never
    sees them as recent.
    `alpha-run-joined` (window `PROJECT_JOINED_WINDOW`): two transcript turns that
    request the Qwen alias and record the Qwen ID as `responseModel`; 1000 tokens
    in, 200 out, peak turn context 700 (400 input + 300 cache read).
    `alpha-run-sybil` (window [10:59:10, 11:00:00]): an unknown alias in the
    ledger and no transcript.
    """
    delegate_dir = make_project(root, "alpha", layout="agentic")
    ledger = write_ledger(
        delegate_dir,
        [
            run_record(
                "alpha-run-joined",
                ts="2026-10-20T10:01:40+00:00",
                wall_seconds=100,
                model=model_record(model_id=ALIAS_QWEN),
            ),
            run_record(
                "alpha-run-sybil",
                ts="2026-10-20T11:00:00+00:00",
                wall_seconds=50,
                model=model_record(model_id=ALIAS_UNKNOWN),
            ),
        ],
    )
    run_dir = write_result_dir(delegate_dir, "alpha-run-joined")
    write_session(
        run_dir,
        [
            session_header(),
            assistant_message(tokens_in=600, tokens_out=120, model=ALIAS_QWEN, response_model=QWEN),
            assistant_message(
                tokens_in=400,
                tokens_out=80,
                cache_read=300,
                model=ALIAS_QWEN,
                response_model=QWEN,
            ),
        ],
    )
    return ledger
