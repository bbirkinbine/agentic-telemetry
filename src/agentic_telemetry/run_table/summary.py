"""What a run-table build found, rendered for stdout.

The summary names run IDs, counts and evidence locations. It never prints a
run's `reason`, which can carry local paths and captured tool output.
"""

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Summary:
    """Counts, orphans and warnings from one run-table build."""

    ledgers_found: int = 0
    rows_per_project: dict[str, int] = field(default_factory=dict)
    reviews_joined: int = 0
    runs_with_tokens: int = 0
    orphans: list[str] = field(default_factory=list)
    unknown_type_counts: Counter[str] = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)
    # Filled by the metrics join (spec 0005). Both counters hold the full
    # vocabulary with zeros, so a reader can see which statuses did not occur.
    server_queried: bool = False
    server_status_counts: Counter[str] = field(default_factory=Counter)
    server_source_counts: Counter[str] = field(default_factory=Counter)

    @property
    def rows_written(self) -> int:
        """Total rows across projects."""
        return sum(self.rows_per_project.values())


def render_summary(summary: Summary) -> str:
    """Return the multi-line stdout summary of a build."""
    lines = [
        f"ledgers found: {summary.ledgers_found}",
        f"rows written: {summary.rows_written}",
    ]
    lines.extend(f"  {project}: {count}" for project, count in summary.rows_per_project.items())
    lines.append(f"reviews joined: {summary.reviews_joined}")
    lines.append(f"runs with token totals: {summary.runs_with_tokens}")
    lines.append(f"orphaned result directories: {len(summary.orphans)}")
    lines.extend(f"  {orphan}" for orphan in summary.orphans)
    if summary.unknown_type_counts:
        counted = ", ".join(
            f"{row_type} x{count}"
            for row_type, count in sorted(summary.unknown_type_counts.items())
        )
        lines.append(f"skipped ledger row types: {counted}")
    if summary.server_queried:
        lines.extend(
            f"server_status {status}: {summary.server_status_counts[status]}"
            for status in summary.server_status_counts
        )
        lines.extend(
            f"server_model_source {source}: {summary.server_source_counts[source]}"
            for source in summary.server_source_counts
        )
    lines.append(f"warnings: {len(summary.warnings)}")
    lines.extend(f"  - {warning}" for warning in summary.warnings)
    return "\n".join(lines)
