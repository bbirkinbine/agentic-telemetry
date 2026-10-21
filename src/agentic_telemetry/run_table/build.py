"""Build the run table from input directories: discover, assemble, dedupe, summarize."""

from collections.abc import Sequence
from pathlib import Path

from agentic_telemetry.run_table.dedupe import merge_runs
from agentic_telemetry.run_table.discovery import find_ledgers
from agentic_telemetry.run_table.join import assemble_ledger
from agentic_telemetry.run_table.row import RunRow
from agentic_telemetry.run_table.summary import Summary


def build_run_table(dirs: Sequence[Path]) -> tuple[list[RunRow], Summary]:
    """Read every delegate ledger under `dirs` into run-table rows.

    Reads evidence only; writes nothing.

    Args:
        dirs: Project roots or collector landing directories.

    Returns:
        One row per distinct run ID, and the build summary.

    Raises:
        NoLedgerError: A directory holds no delegate ledger.
        ConflictingRunError: One run ID carries different data in two places.
    """
    discovered = find_ledgers(dirs)
    summary = Summary(ledgers_found=len(discovered))
    all_rows: list[RunRow] = []
    orphan_ids: set[str] = set()
    for ledger in discovered:
        assembled = assemble_ledger(ledger)
        all_rows.extend(assembled.rows)
        for orphan in assembled.orphans:
            # A project root and its landing copy hold the same orphan; list it once.
            if orphan not in orphan_ids:
                orphan_ids.add(orphan)
                summary.orphans.append(f"{ledger.project}/{orphan}")
        summary.unknown_type_counts.update(assembled.unknown_type_counts)
        summary.warnings.extend(
            warning for warning in assembled.warnings if warning not in summary.warnings
        )

    rows = merge_runs(all_rows)
    for row in rows:
        summary.rows_per_project[row.project] = summary.rows_per_project.get(row.project, 0) + 1
    summary.reviews_joined = sum(1 for row in rows if row.review_count)
    summary.runs_with_tokens = sum(1 for row in rows if row.tokens_in is not None)
    return rows, summary
