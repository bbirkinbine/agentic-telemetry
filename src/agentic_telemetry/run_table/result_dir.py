"""Read per-run result directories: `results/<run>/result.json` and `state.json`."""

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from agentic_telemetry.run_table.json_values import (
    JsonObject,
    as_object,
    object_list,
    opt_int,
    opt_str,
)


@dataclass(frozen=True)
class StateSummary:
    """The parts of a delegate-run-v2 `state.json` the run table keeps."""

    phase: str | None
    retries: int
    compactions: int


@dataclass(frozen=True)
class ResultDir:
    """One `results/<run>/` directory."""

    result_json: JsonObject | None
    state: StateSummary | None
    warnings: tuple[str, ...] = ()


def scan_results(results_dir: Path) -> dict[str, ResultDir]:
    """Read every run directory under `results_dir`.

    Args:
        results_dir: A delegate `results/` directory.

    Returns:
        The run directories keyed by directory name (the run ID). A directory
        without `result.json` is still listed, with `result_json` None.
    """
    if not results_dir.is_dir():
        return {}
    scanned: dict[str, ResultDir] = {}
    for run_dir in sorted(path for path in results_dir.iterdir() if path.is_dir()):
        warnings: list[str] = []
        result_json = _read_object(run_dir / "result.json", warnings)
        state_json = _read_object(run_dir / "state.json", warnings)
        state = _summarize_state(state_json) if state_json is not None else None
        scanned[run_dir.name] = ResultDir(result_json, state, tuple(warnings))
    return scanned


def orphaned_result_dirs(
    results: Mapping[str, ResultDir], ledger_run_ids: Collection[str]
) -> list[str]:
    """Return run IDs whose directory has neither a ledger row nor a `result.json`.

    These are interrupted runs: they become no row in the run table.
    """
    return sorted(
        run_id
        for run_id, result in results.items()
        if result.result_json is None and run_id not in ledger_run_ids
    )


def diff_keys(first: Mapping[str, object], second: Mapping[str, object]) -> list[str]:
    """Return the sorted keys whose values differ or that only one mapping has."""
    keys = set(first) | set(second)
    return sorted(
        key for key in keys if key not in first or key not in second or first[key] != second[key]
    )


def _read_object(path: Path, warnings: list[str]) -> JsonObject | None:
    """Read a JSON object file; a missing file is None, a bad one is None plus a warning."""
    if not path.is_file():
        return None
    try:
        decoded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:  # ValueError covers JSON and UTF-8 decode errors
        warnings.append(f"{path}: skipped unreadable file ({error})")
        return None
    record = as_object(decoded)
    if record is None:
        warnings.append(f"{path}: skipped a file that is not a JSON object")
    return record


def _summarize_state(state: JsonObject) -> StateSummary:
    """Keep the phase and sum retries and compactions over the run's segments."""
    segments = object_list(state.get("segments"))
    return StateSummary(
        phase=opt_str(state.get("phase")),
        retries=sum(opt_int(segment.get("retries")) or 0 for segment in segments),
        compactions=sum(opt_int(segment.get("compactions")) or 0 for segment in segments),
    )
