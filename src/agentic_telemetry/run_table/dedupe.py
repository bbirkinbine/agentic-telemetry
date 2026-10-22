"""Collapse a run reached through several inputs into one row, or refuse to."""

import dataclasses
from collections import Counter
from collections.abc import Mapping, Sequence

from agentic_telemetry.run_table.row import RunRow

# Where a row was read from; the same run through two inputs differs only here.
LOCATION_FIELDS = ("project", "ledger_path")

# Columns derived from a run's reviews. A stale landing-zone copy can lack a
# review the live ledger has since gained, so these are compared through the
# review multisets instead (spec 0004).
REVIEW_FIELDS = (
    "review_verdict",
    "review_ts",
    "review_count",
    "first_failure_phase",
    "failure_cause",
)

# One run's reviews as `(ts, verdict)` pairs, counted, so a repeated review counts twice.
ReviewMultiset = Counter[tuple[str | None, str | None]]

# Review multisets keyed by `(ledger_path, run_id)`.
ReviewsByLedger = Mapping[tuple[str, str], ReviewMultiset]


class ConflictingRunError(Exception):
    """The same run ID carries different data in two places."""

    def __init__(self, run_id: str, first: RunRow, second: RunRow, differing: str) -> None:
        """Record the run and both ledgers, naming what differs."""
        self.run_id = run_id
        super().__init__(
            f"run {run_id} differs between {first.ledger_path} and {second.ledger_path} "
            f"({differing}); refusing to choose one"
        )


def merge_runs(
    rows: Sequence[RunRow], reviews: ReviewsByLedger | None = None
) -> tuple[list[RunRow], list[str]]:
    """Keep one row per run ID, in first-seen order.

    Rows with the same run ID collapse when they match on every column except
    `project` and `ledger_path`; the first one is kept. When their run columns
    match but their reviews differ, and one ledger's reviews of the run are
    contained in the other's, the row with more reviews wins in the first-seen
    position: the other is a stale landing-zone copy (spec 0004).

    Args:
        rows: Rows from every ledger, in discovery order.
        reviews: Each run's review multiset, keyed by `(ledger_path, run_id)`. A
            missing key means the run has no reviews in that ledger.

    Returns:
        One row per distinct run ID, and one warning per stale copy replaced or
        dropped.

    Raises:
        ConflictingRunError: Two rows with the same run ID differ in a run column,
            or their reviews disagree rather than one containing the other.
    """
    reviews = reviews or {}
    kept: dict[str, RunRow] = {}
    warnings: list[str] = []
    for row in rows:
        first = kept.get(row.run_id)
        if first is None:
            kept[row.run_id] = row
            continue
        run_fields = _differing_fields(first, row, LOCATION_FIELDS + REVIEW_FIELDS)
        if run_fields:
            raise ConflictingRunError(row.run_id, first, row, f"columns: {', '.join(run_fields)}")
        review_columns_match = not _differing_fields(first, row, LOCATION_FIELDS)
        kept_reviews = _reviews_of(reviews, first)
        new_reviews = _reviews_of(reviews, row)
        if kept_reviews == new_reviews:
            if review_columns_match:
                continue
            # Equal multisets give equal review columns; a mismatch means the
            # caller passed no multisets for these rows, so nothing says which is stale.
            raise ConflictingRunError(row.run_id, first, row, "reviews disagree")
        if kept_reviews <= new_reviews:
            kept[row.run_id] = row
            warnings.append(_stale_warning(row, first, new_reviews, kept_reviews))
        elif new_reviews <= kept_reviews:
            warnings.append(_stale_warning(first, row, kept_reviews, new_reviews))
        else:
            raise ConflictingRunError(row.run_id, first, row, "reviews disagree")
    return list(kept.values()), warnings


def _reviews_of(reviews: ReviewsByLedger, row: RunRow) -> ReviewMultiset:
    """Return the review multiset of `row`'s run in `row`'s ledger, empty when unreviewed."""
    return reviews.get((row.ledger_path, row.run_id), Counter())


def _stale_warning(
    winner: RunRow, stale: RunRow, winner_reviews: ReviewMultiset, stale_reviews: ReviewMultiset
) -> str:
    """Name the run, the ledger kept, the stale ledger and both review counts."""
    return (
        f"run {winner.run_id}: kept {winner.ledger_path} "
        f"({winner_reviews.total()} review(s)) over the stale copy {stale.ledger_path} "
        f"({stale_reviews.total()} review(s))"
    )


def _differing_fields(first: RunRow, second: RunRow, ignored: Sequence[str]) -> list[str]:
    """Return the columns outside `ignored` whose values differ between two rows."""
    return [
        field.name
        for field in dataclasses.fields(RunRow)
        if field.name not in ignored and getattr(first, field.name) != getattr(second, field.name)
    ]
