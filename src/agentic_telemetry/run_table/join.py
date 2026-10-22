"""Assemble one ledger's run rows: join reviews, result directories and transcripts."""

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agentic_telemetry.run_table.dedupe import ReviewMultiset
from agentic_telemetry.run_table.discovery import DiscoveredLedger
from agentic_telemetry.run_table.failure_phase import delegated_phase, first_failure
from agentic_telemetry.run_table.json_values import JsonObject, opt_str
from agentic_telemetry.run_table.ledger import ReviewRecord, parse_ledger
from agentic_telemetry.run_table.result_dir import (
    ResultDir,
    diff_keys,
    orphaned_result_dirs,
    scan_results,
)
from agentic_telemetry.run_table.row import FailureColumns, ReviewSummary, RunRow, build_row
from agentic_telemetry.run_table.session_usage import read_session_usage


@dataclass
class LedgerResult:
    """The rows one ledger contributes, and what was found on the way."""

    rows: list[RunRow] = field(default_factory=list)
    reviews_joined: int = 0
    orphans: list[str] = field(default_factory=list)
    unknown_type_counts: Counter[str] = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)
    # Every review of each run as `(ts, verdict)` pairs, so a stale landing-zone
    # copy can be told from a conflicting one (spec 0004). Unreviewed runs are absent.
    review_keys: dict[str, ReviewMultiset] = field(default_factory=dict)


def assemble_ledger(discovered: DiscoveredLedger) -> LedgerResult:
    """Build the run rows for one discovered ledger.

    The ledger row wins over a differing `result.json` (with a warning naming
    the differing keys). A `result.json` fills in a run the ledger lacks. A
    result directory with neither is an orphan: listed and warned, not a row.

    Args:
        discovered: The ledger and its results directory.

    Returns:
        Rows in ledger order, then result-only runs; joined-review count;
        orphaned run IDs; unknown row-type counts; and every warning.
    """
    contents = parse_ledger(discovered.path)
    results = scan_results(discovered.results_dir) if discovered.results_dir else {}
    result = LedgerResult(
        unknown_type_counts=contents.unknown_type_counts,
        warnings=list(contents.warnings),
    )
    for result_dir in results.values():
        result.warnings.extend(result_dir.warnings)

    records = _run_records(contents.runs, results, result.warnings)
    run_ids = {opt_str(record.get("run")) or "" for record in records}
    reviews = _latest_reviews(contents.reviews, result.warnings)
    for review in contents.reviews:
        result.review_keys.setdefault(review.run_id, Counter())[(review.ts, review.verdict)] += 1
    for review_run_id, (review, _) in reviews.items():
        if review_run_id not in run_ids:
            result.warnings.append(
                f"{discovered.path}:{review.line_no}: review for run {review_run_id} has no run row"
            )

    for record in records:
        row = _assemble_row(discovered, record, reviews, results, result.warnings)
        result.rows.append(row)
    result.reviews_joined = len({row.run_id for row in result.rows if row.review_count})

    ledger_run_ids = {opt_str(record.get("run")) or "" for _, record in contents.runs}
    result.orphans = orphaned_result_dirs(results, ledger_run_ids)
    for orphan in result.orphans:
        result.warnings.append(
            f"orphaned result directory {discovered.project}/{orphan}: "
            "no ledger row and no result.json"
        )
    return result


def _run_records(
    ledger_runs: Sequence[tuple[int, JsonObject]],
    results: dict[str, ResultDir],
    warnings: list[str],
) -> list[JsonObject]:
    """Return ledger run rows in order, then `result.json` rows the ledger lacks."""
    records: list[JsonObject] = []
    ledger_ids: set[str] = set()
    for _, record in ledger_runs:
        run_id = opt_str(record.get("run")) or ""
        ledger_ids.add(run_id)
        records.append(record)
        result_json = results[run_id].result_json if run_id in results else None
        if result_json is not None and result_json != record:
            keys = ", ".join(diff_keys(record, result_json))
            warnings.append(
                f"run {run_id}: result.json differs from the ledger row in {keys}; "
                "kept the ledger row"
            )
    for run_id, result_dir in results.items():
        if run_id in ledger_ids or result_dir.result_json is None:
            continue
        # The directory is where this run's transcript and state.json live, so its
        # name is the run ID; a disagreeing `run` field is reported, not followed.
        recorded_id = result_dir.result_json.get("run")
        if recorded_id != run_id:
            warnings.append(
                f"run {run_id}: result.json names run {recorded_id!r}; used the directory name"
            )
        records.append({**result_dir.result_json, "run": run_id})
    return records


def _latest_reviews(
    reviews: Sequence[ReviewRecord], warnings: list[str]
) -> dict[str, tuple[ReviewRecord, int]]:
    """Return each run's latest review by timestamp (later line on a tie) and its count."""
    by_run: defaultdict[str, list[ReviewRecord]] = defaultdict(list)
    for review in reviews:
        by_run[review.run_id].append(review)
    latest: dict[str, tuple[ReviewRecord, int]] = {}
    for run_id, run_reviews in by_run.items():
        newest = max(
            run_reviews, key=lambda review: (_review_time(review, warnings), review.line_no)
        )
        latest[run_id] = (newest, len(run_reviews))
    return latest


def _review_time(review: ReviewRecord, warnings: list[str]) -> datetime:
    """Parse a review timestamp; a missing or unparsable one sorts first, with a warning."""
    earliest = datetime.min.replace(tzinfo=UTC)
    if review.ts is None:
        return earliest
    try:
        parsed = datetime.fromisoformat(review.ts)
    except ValueError:
        warnings.append(f"run {review.run_id}: review timestamp {review.ts!r} is not ISO 8601")
        return earliest
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _assemble_row(
    discovered: DiscoveredLedger,
    record: JsonObject,
    reviews: dict[str, tuple[ReviewRecord, int]],
    results: dict[str, ResultDir],
    warnings: list[str],
) -> RunRow:
    """Join one run record to its review, state, transcripts and derived failure."""
    run_id = opt_str(record.get("run")) or ""
    state = results[run_id].state if run_id in results else None
    usage = None
    if discovered.results_dir is not None:
        usage = read_session_usage(discovered.results_dir / run_id / "session")
    if usage is not None:
        warnings.extend(usage.warnings)

    review = None
    if run_id in reviews:
        latest, count = reviews[run_id]
        review = ReviewSummary(verdict=latest.verdict, ts=latest.ts, count=count)

    phase, phase_warning = delegated_phase(
        opt_str(record.get("bucket")), state.phase if state else None
    )
    failure_phase, cause, outcome_warning = first_failure(
        opt_str(record.get("outcome")),
        review.verdict if review else None,
        phase,
        reviewed=review is not None,
    )
    for warning in (phase_warning, outcome_warning):
        if warning is not None:
            warnings.append(f"run {run_id}: {warning}")

    return build_row(
        project=discovered.project,
        ledger_path=str(discovered.path),
        run=record,
        review=review,
        usage=usage,
        state=state,
        failure=FailureColumns(phase, failure_phase, cause),
    )
