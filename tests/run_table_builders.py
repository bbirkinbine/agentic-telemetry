"""Synthetic delegate-runner evidence for run-table tests.

Every value here is made up: run IDs, hashes, model names and the
`gpu.example` endpoint (RFC 2606). Paths are always under a pytest
`tmp_path`. No real ledger line, transcript or hostname is copied in.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

JsonObject = dict[str, object]

PLACEHOLDER_ENDPOINT = "gpu.example"
LANDING_HOST = "laptop.example"


def run_record(
    run_id: str = "run-alpha-one",
    *,
    drop: Sequence[str] = (),
    **overrides: object,
) -> JsonObject:
    """Return a ledger `type: "run"` row shaped like the delegate runner's output.

    Keyword overrides replace top-level keys; names in `drop` are removed so a
    test can model early rows that lack a key such as `wall_seconds`.
    """
    record: JsonObject = {
        "type": "run",
        "ts": "2026-10-20T10:00:00+00:00",
        "run": run_id,
        "mode": "per-file",
        "selection": None,
        "fingerprint": "fp-0a1b2c3d4e5f",
        "qualified": True,
        "packet": "packets/example-packet.md",
        "packet_sha256": "a" * 64,
        "target": "src/example_module.py",
        "bucket": "small",
        "snapshot_sha256": "b" * 64,
        "harness": "delegate-run-v1",
        "model": model_record(),
        "attempts": [
            {"attempt": 1, "outcome": "gate-fail", "detail": "unit tests red", "seconds": 30},
            {"attempt": 2, "outcome": "green", "detail": "gate green", "seconds": 12},
        ],
        "outcome": "green",
        "reason": "reason-text-not-for-stdout",
        "gate": "pass",
        "unverified_steps": ["manual smoke check"],
        "review": None,
        "wall_seconds": 50,
    }
    record.update(overrides)
    for key in drop:
        record.pop(key, None)
    return record


def model_record(**overrides: object) -> JsonObject:
    """Return a run row's `model` object with a placeholder endpoint."""
    model: JsonObject = {
        "name": "example-coder",
        "model_id": "example-coder-7b-instruct",
        "quant": "q4_k_m",
        "runtime": "example-runtime",
        "endpoint": PLACEHOLDER_ENDPOINT,
        "request": '{"temperature":0.2, "top_p": 0.95}',
    }
    model.update(overrides)
    return model


def review_record(
    run_id: str,
    verdict: str = "pass",
    ts: str = "2026-10-20T12:00:00+00:00",
) -> JsonObject:
    """Return a ledger `type: "review"` row."""
    return {"type": "review", "ts": ts, "run": run_id, "verdict": verdict, "review_changes": None}


def write_ledger(delegate_dir: Path, lines: Iterable[Mapping[str, object] | str]) -> Path:
    """Write `ledger.jsonl` in `delegate_dir`; str items are written raw (malformed lines)."""
    delegate_dir.mkdir(parents=True, exist_ok=True)
    rendered = [line if isinstance(line, str) else json.dumps(line) for line in lines]
    ledger = delegate_dir / "ledger.jsonl"
    ledger.write_text("".join(f"{line}\n" for line in rendered), encoding="utf-8")
    return ledger


def write_result_dir(
    delegate_dir: Path,
    run_id: str,
    *,
    result: Mapping[str, object] | None = None,
    extra_files: Sequence[str] = ("prompt.md", "harness.log"),
) -> Path:
    """Create `results/<run_id>/`, optionally with `result.json`, and return it."""
    run_dir = delegate_dir / "results" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in extra_files:
        (run_dir / name).write_text("synthetic placeholder\n", encoding="utf-8")
    if result is not None:
        (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return run_dir


def assistant_message(
    *,
    tokens_in: int,
    tokens_out: int,
    cache_read: int = 0,
    cache_write: int = 0,
    reasoning: int = 0,
    message_id: str = "m-assistant",
) -> JsonObject:
    """Return one Pi transcript line for an assistant message carrying `usage`."""
    return {
        "type": "message",
        "id": message_id,
        "parentId": "m-parent",
        "timestamp": "2026-10-20T10:01:00.000Z",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "synthetic reply"}],
            "usage": {
                "input": tokens_in,
                "output": tokens_out,
                "cacheRead": cache_read,
                "cacheWrite": cache_write,
                "reasoning": reasoning,
                "totalTokens": tokens_in + tokens_out + cache_read + cache_write + reasoning,
            },
        },
    }


def session_header(session_id: str = "session-0001") -> JsonObject:
    """Return the first line of a Pi transcript."""
    return {
        "type": "session",
        "version": 3,
        "id": session_id,
        "timestamp": "2026-10-20T10:00:00.000Z",
        "cwd": "worktree-placeholder",
    }


def other_role_message(role: str) -> JsonObject:
    """Return a transcript message line for a non-assistant role (no usage to count)."""
    return {
        "type": "message",
        "id": f"m-{role}",
        "parentId": "m-parent",
        "timestamp": "2026-10-20T10:00:30.000Z",
        "message": {"role": role, "content": [{"type": "text", "text": "synthetic"}]},
    }


def write_session(
    run_dir: Path,
    lines: Iterable[Mapping[str, object] | str],
    *,
    name: str = "session-0001.jsonl",
) -> Path:
    """Write one transcript file under `run_dir/session/`; str items are written raw."""
    session_dir = run_dir / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    rendered = [line if isinstance(line, str) else json.dumps(line) for line in lines]
    path = session_dir / name
    path.write_text("".join(f"{line}\n" for line in rendered), encoding="utf-8")
    return path


def write_state(
    run_dir: Path,
    *,
    phase: str,
    segments: Sequence[tuple[int, int]],
    state: str = "green",
) -> Path:
    """Write a delegate-run-v2 `state.json`; segments are (retries, compactions) pairs."""
    run_dir.mkdir(parents=True, exist_ok=True)
    payload: JsonObject = {
        "run": run_dir.name,
        "state": state,
        "phase": phase,
        "harness_version": "delegate-run-v2",
        "segments": [
            {
                "segment": f"{index:04d}",
                "retries": retries,
                "compactions": compactions,
                "end": "completed",
                "seconds": 100 + index,
            }
            for index, (retries, compactions) in enumerate(segments, start=1)
        ],
    }
    path = run_dir / "state.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def make_project(
    root: Path,
    name: str,
    layout: Literal["agentic", "landing"] = "agentic",
) -> Path:
    """Create a project skeleton and return the directory that will hold `ledger.jsonl`.

    `agentic`: `<root>/<name>/.agentic/delegate/` (a bootstrapped project root).
    `landing`: `<root>/landing/laptop.example/<name>/` with a `results/` directory,
    the collector landing copy that flattens `.agentic/delegate/`.
    """
    if layout == "agentic":
        delegate_dir = root / name / ".agentic" / "delegate"
    else:
        delegate_dir = root / "landing" / LANDING_HOST / name
    (delegate_dir / "results").mkdir(parents=True, exist_ok=True)
    return delegate_dir


@dataclass(frozen=True)
class EvidenceTree:
    """A two-project evidence tree with known counts for end-to-end tests.

    alpha (bootstrapped layout): 4 rows (3 ledger rows + 1 filled in from
    result.json), 2 joined reviews, 1 orphaned result directory, 1 malformed
    ledger line, 1 review for a run that does not exist.
    beta (landing layout): 1 handoff row with a transcript, state.json and a review.
    """

    root: Path
    alpha_ledger: Path
    beta_ledger: Path
    malformed_line_no: int
    alpha_run_ids: tuple[str, ...]
    beta_run_ids: tuple[str, ...]
    orphan_run_id: str
    ghost_review_run_id: str
    reviews_joined: int
    runs_with_tokens: int
