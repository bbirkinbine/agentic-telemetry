"""Derive the delegated phase and the first failure phase of a delegate run.

The evidence has no failure-phase field, so the phase comes from the run's
outcome and its review verdict (spec 0001). Phase names follow the transition
failure matrix in `analysis/README.md`.
"""

MATRIX_PHASES = ("spec", "plan", "test-first", "implement", "gate", "review")

# Per-file delegate runs (harness v1) implement one target file.
PER_FILE_BUCKETS = frozenset({"small", "medium"})

PHASE_BUCKET_PREFIX = "phase:"


def delegated_phase(bucket: str | None, state_phase: str | None) -> tuple[str | None, str | None]:
    """Return the phase a run was delegated, and a warning when it is doubtful.

    The phase comes from, in order: `state.json`'s `phase`, the `phase:` bucket
    prefix, or `implement` for the per-file buckets `small` and `medium`.

    Args:
        bucket: The ledger row's `bucket`.
        state_phase: The `phase` from the run's `state.json`, if it has one.

    Returns:
        A (phase, warning) pair. The phase is None when nothing names it. A phase
        outside the transition matrix is kept as recorded, with a warning.
    """
    if state_phase is not None:
        phase = state_phase
    elif bucket is not None and bucket.startswith(PHASE_BUCKET_PREFIX):
        phase = bucket.removeprefix(PHASE_BUCKET_PREFIX)
    elif bucket in PER_FILE_BUCKETS:
        phase = "implement"
    else:
        return None, f"cannot tell the delegated phase from bucket {bucket!r}"
    if phase not in MATRIX_PHASES:
        return phase, f"delegated phase {phase!r} is not one of {', '.join(MATRIX_PHASES)}"
    return phase, None


def first_failure(
    outcome: str | None,
    verdict: str | None,
    phase: str | None,
    *,
    reviewed: bool = False,
) -> tuple[str | None, str | None, str | None]:
    """Return (first failure phase, cause, warning) for one run.

    - `green` with review `pass`, or not reviewed: no failure.
    - `green` reviewed with no verdict: phase `unknown`, with a warning, so the run
      cannot pass for a clean review.
    - `green` with any other verdict: fails at `review`, cause = the verdict.
    - `needs_frontier`: fails at `gate`, cause = `needs_frontier`.
    - `integrity`: fails at the delegated phase, cause = `integrity`; phase
      `unknown` when the delegated phase cannot be told.
    - Any other outcome: phase `unknown`, cause = the outcome, with a warning.

    Args:
        outcome: The run's `outcome`.
        verdict: The latest review verdict, or None when it has none.
        phase: The delegated phase from `delegated_phase`.
        reviewed: Whether the run has any review row.

    Returns:
        The first failure phase (None when the run did not fail), its cause, and a
        warning when the outcome or review is not one the table recognizes.
    """
    if outcome == "green":
        if verdict is None and reviewed:
            return "unknown", "review-without-verdict", "latest review has no verdict"
        if verdict is None or verdict == "pass":
            return None, None, None
        return "review", verdict, None
    if outcome == "needs_frontier":
        return "gate", "needs_frontier", None
    if outcome == "integrity":
        # delegated_phase already warned when it could not tell the phase.
        return phase or "unknown", "integrity", None
    return "unknown", outcome, f"unrecognized outcome {outcome!r}"
