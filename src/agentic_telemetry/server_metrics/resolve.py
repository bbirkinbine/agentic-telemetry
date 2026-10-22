"""Which served model a run ran on, without a hand-kept alias table (spec 0005).

Pure: names, the set VictoriaMetrics knows, and which models' counters moved are
all read by the caller. The first rule that applies decides, so a name the run
itself recorded always beats an inference from counters.
"""

from collections.abc import Collection, Iterable, Sequence

SOURCE_SERVED = "served"
SOURCE_REQUESTED = "requested"
SOURCE_INFERRED = "inferred"
SOURCE_NONE = "none"


def resolve_model(
    requested: Sequence[str],
    served: Sequence[str],
    ledger_model_id: str | None,
    known: frozenset[str],
    moved: Collection[str],
) -> tuple[str | None, str]:
    """Return the model ID a run ran on and which rule resolved it.

    Args:
        requested: The `model` names in the run's transcript, first-seen order.
        served: The `responseModel` names in the run's transcript, first-seen
            order. Pi records one only when it differs from the requested name.
        ledger_model_id: The ID the delegate ledger recorded for this run, which
            may be a profile alias rather than a served ID.
        known: Every `model_id` VictoriaMetrics has seen from the inference host.
        moved: Model IDs whose token counters rose during the run's window.

    Returns:
        `(model_id, source)` where source is `served`, `requested`, `inferred`
        or `none`. A served name is trusted only when it is known and no
        requested name, nor the ledger's ID, is a *different* known ID: the GLM
        builds answer with their own name whatever was asked for, so
        `glm-5.3-flash` alone cannot say which GLM served the run.
    """
    served_known = _distinct(name for name in served if name in known)
    recorded = [*requested, *([ledger_model_id] if ledger_model_id is not None else [])]
    if len(served_known) == 1 and not _contradicts(recorded, served_known[0], known):
        return served_known[0], SOURCE_SERVED

    requested_known = _distinct(
        [name for name in requested if name in known]
        + ([ledger_model_id] if ledger_model_id is not None and ledger_model_id in known else [])
    )
    if len(requested_known) == 1:
        return requested_known[0], SOURCE_REQUESTED

    # Rule 3 only for a transcript with no model names (D8): a run that named an
    # unknown model, such as a build without metrics, is uncovered, not whichever
    # neighbour happened to move. The caller keeps an inference only when the
    # run's tokens reconcile exactly.
    moved_known = _distinct(name for name in sorted(moved) if name in known)
    if not requested and not served and len(moved_known) == 1:
        return moved_known[0], SOURCE_INFERRED

    return None, SOURCE_NONE


def _distinct(names: Iterable[str]) -> tuple[str, ...]:
    """Return `names` without repeats, keeping first-seen order."""
    return tuple(dict.fromkeys(names))


def _contradicts(requested: Sequence[str], candidate: str, known: frozenset[str]) -> bool:
    """True when a requested name is a known model other than `candidate`."""
    return any(name in known and name != candidate for name in requested)
